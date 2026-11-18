"""Bounded deterministic parsing/citations, adapted from the v2 import readers."""
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
from pathlib import PurePosixPath
from app.operations.errors import OperationError

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 110_000
ALLOWED = {
    '.txt': {'text/plain', 'application/octet-stream'},
    '.md': {'text/markdown', 'text/plain', 'application/octet-stream'},
    '.markdown': {'text/markdown', 'text/plain', 'application/octet-stream'},
    '.pdf': {'application/pdf', 'application/octet-stream'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip', 'application/octet-stream'},
}


def normalize(documents):
    roles = [d.role for d in documents]
    if not {'brd', 'srs'} <= set(roles) or len(set(roles)) != len(roles) or len({d.id for d in documents}) != len(documents):
        raise OperationError("Provide distinct document IDs, one BRD, one SRS and optionally one design specification.", 422)
    blocks, warnings, total = [], [], 0
    for document in documents:
        filename = document.filename
        suffix = PurePosixPath(filename).suffix.lower()
        if any(c in filename for c in '/\\\x00\r\n') or suffix not in ALLOWED or document.content_type.split(';')[0] not in ALLOWED[suffix]:
            raise OperationError("Unsupported document filename or content type.", 415)
        try:
            data = base64.b64decode(document.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise OperationError("Document must be valid base64.", 422) from None
        total += len(data)
        if not data or total > MAX_BYTES:
            raise OperationError("The document set must contain 1 byte to 10 MiB.", 413)
        if suffix == '.docx':
            from app.operations.docx_reader import extract_docx
            text, notes = extract_docx(data)
            warnings.extend(notes)
        elif suffix == '.pdf':
            if not data.startswith(b'%PDF-'):
                raise OperationError("Content is not a PDF document.", 415)
            try:
                process = subprocess.run([sys.executable, '-m', 'app.operations.pdf_reader'], input=data,
                    capture_output=True, timeout=20, check=True)
                result = json.loads(process.stdout)
                if 'error' in result:
                    raise OperationError(result['error'], result.get('status', 400))
                text = '\n\n'.join(p['text'] for p in result['pages'])
            except (subprocess.SubprocessError, ValueError, KeyError):
                raise OperationError("PDF could not be safely parsed; upload a text version.", 415) from None
        else:
            try:
                text = data.decode('utf-8-sig')
            except UnicodeError:
                raise OperationError("Text documents must use UTF-8.", 415) from None
        if not text.strip() or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
            raise OperationError("Document has no readable text or contains binary data.", 415)
        if len(text) > 90_000:
            raise OperationError("Document text exceeds the size limit.", 413)
        # Model chooses passage IDs; excerpts and offsets are attached by this server.
        for start in range(0, len(text), 2000):
            excerpt = text[start:start + 2000]
            if excerpt.strip():
                blocks.append({'id': f'{document.id}:{start}', 'document_id': document.id, 'role': document.role,
                               'start': start, 'end': start + len(excerpt), 'text': excerpt})
    if sum(len(b['text']) for b in blocks) > MAX_TEXT:
        raise OperationError("Combined document text exceeds 110,000 characters.", 413)
    return blocks, warnings
