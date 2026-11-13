"""Bounded extraction of visible main-body DOCX text/tables; no Office execution."""
import io
import zipfile
import zlib
import xml.etree.ElementTree as ET
from app.capabilities.errors import CapabilityError as ServiceError

MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_MEMBERS = 512
NAMESPACES = {'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
              'http://purl.oclc.org/ooxml/wordprocessingml/main'}


def _name(element):
    return element.tag.rsplit('}', 1)[-1]


def _paragraph(element):
    parts = []
    def walk(node):
        name = _name(node)
        if name in {'del', 'moveFrom'}: return
        if name == 't': parts.append(node.text or ''); return
        if name == 'tab': parts.append('\t'); return
        if name in {'br', 'cr'}: parts.append('\n'); return
        for child in node: walk(child)
    walk(element)
    return ''.join(parts)


def extract_docx(data: bytes):
    warnings = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                raise ServiceError('Word archive exceeds the extraction size/entry limits.', 413)
            names = [item.filename for item in members]
            if len(names) != len(set(names)) or any(item.flag_bits & 1 for item in members):
                raise ServiceError('Encrypted or ambiguous Word archives are not supported.', 415)
            if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
                raise ServiceError('File is not a valid DOCX Word document.', 415)
            if any(name.lower().endswith('vbaproject.bin') for name in names):
                raise ServiceError('Macro-containing Word packages are not supported. Upload a plain .docx or text version.', 415)
            info = archive.getinfo('word/document.xml')
            if info.file_size > MAX_XML_BYTES:
                raise ServiceError('Word document XML exceeds the 4 MiB extraction limit.', 413)
            with archive.open(info) as stream:
                xml = stream.read(MAX_XML_BYTES + 1)
            if len(xml) > MAX_XML_BYTES: raise ServiceError('Word document XML exceeds extraction limits.', 413)
            declaration_check = xml.replace(b'\x00', b'').upper()
            if b'<!DOCTYPE' in declaration_check or b'<!ENTITY' in declaration_check:
                raise ServiceError('Word XML entity declarations are not supported.', 415)
            root = ET.fromstring(xml)
            namespace = root.tag[1:].split('}', 1)[0] if root.tag.startswith('{') else ''
            if namespace not in NAMESPACES or _name(root) != 'document':
                raise ServiceError('Unsupported Word document XML structure.', 415)
            body = root.find(f'{{{namespace}}}body')
            if body is None: raise ServiceError('Word document has no readable body.', 400)
            if any(name.startswith('word/media/') for name in names):
                warnings.append('Embedded images/diagrams are not interpreted; only extracted text is analyzed.')
            if any(name.startswith(('word/header', 'word/footer', 'word/footnotes', 'word/endnotes', 'word/comments')) for name in names):
                warnings.append('Headers, footers, notes and comments are outside main-body text extraction.')
            if any(_name(node) in {'ins', 'del', 'moveFrom', 'moveTo'} for node in root.iter()):
                warnings.append('Tracked changes are present; explicitly deleted runs/wrappers are excluded. Accept/review revisions before treating this as an approved baseline.')
            blocks = []
            def walk_blocks(node):
                kind = _name(node)
                if kind in {'del', 'moveFrom'}: return
                if kind == 'p':
                    blocks.append(_paragraph(node)); return
                if kind == 'tbl':
                    for row in node:
                        if _name(row) != 'tr': continue
                        cells = []
                        for cell in row:
                            if _name(cell) != 'tc': continue
                            previous = len(blocks)
                            for child in cell: walk_blocks(child)
                            cell_lines = blocks[previous:]
                            del blocks[previous:]
                            cells.append('\n'.join(cell_lines))
                        blocks.append('\t'.join(cells))
                    return
                for child in node: walk_blocks(child)
            walk_blocks(body)
            text = '\n\n'.join(blocks)
            if not text.strip(): raise ServiceError('Word document contains no readable main-body text. Image-only documents need OCR.', 400)
            if len(text) > 90_000: raise ServiceError('Extracted Word text exceeds 90,000 characters.', 413)
            return text, warnings
    except ServiceError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError, RecursionError, NotImplementedError, OSError, ValueError, zlib.error) as exc:
        raise ServiceError('Word document is malformed or cannot be safely extracted. Upload a text version.', 415) from exc
