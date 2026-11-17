"""Bounded PDF extraction subprocess. No user-controlled commands or paths."""
import io
import json
import sys


def main():
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    try:
        from pypdf import PdfReader
    except ImportError:
        print(json.dumps({'error': 'PDF parsing dependency is unavailable. Install backend dependencies (pypdf).', 'status': 503}))
        return
    try:
        data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024: raise ValueError('PDF exceeds 10 MiB.')
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted: raise ValueError('Encrypted PDFs are not supported. Upload an unencrypted document.')
        if len(reader.pages) > 50: raise ValueError('PDFs are limited to 50 pages.')
        pages = []
        total = 0
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            total += len(text)
            if total > 90_000: raise ValueError('Extracted PDF text exceeds 90,000 characters.')
            if text.strip(): pages.append({'page': index + 1, 'text': text})
        if not pages: raise ValueError('No selectable text found. Scanned PDFs require OCR; upload Markdown or text instead.')
        print(json.dumps({'pages': pages}))
    except ValueError as exc:
        print(json.dumps({'error': str(exc), 'status': 400}))
    except Exception:
        print(json.dumps({'error': 'PDF is malformed or cannot be safely extracted. Upload a text or Markdown version.', 'status': 400}))

if __name__ == '__main__': main()
