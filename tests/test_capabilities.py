import base64
import json
from dataclasses import replace
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from app.main import create_app
from app.capabilities.service import Capabilities
from app.capabilities.extraction import Extractor
from app.capabilities.schemas import VerificationResponse
from app.capabilities.models import CapabilityOperation, CapabilityUsage
from app.capabilities.documents import normalize
from app.capabilities.errors import CapabilityError
from app.capabilities.schemas import ExtractionRequest
from tests.test_intake import database, config, HEADERS


def payload():
    return {'request_id': str(uuid4()), 'documents': [
        {'id': 'doc-brd', 'role': 'brd', 'filename': 'brd.txt', 'content_type': 'text/plain',
         'content_base64': base64.b64encode(b'The application shall display login.').decode()},
        {'id': 'doc-srs', 'role': 'srs', 'filename': 'srs.md', 'content_type': 'text/markdown',
         'content_base64': base64.b64encode(b'Users shall sign in using email and password.').decode()}]}


class Verifier:
    def configured(self): pass
    async def verify(self, request):
        return VerificationResponse(request_id=request.request_id, environment_id=request.environment_id,
            revision=request.revision, status='verified', reason='success_observed')


def factory(database, handler):
    settings = config(extraction_api_key='synthetic-model-key', extraction_model='test-model')
    return create_app(settings, database, start_delivery=False,
        capabilities_factory=lambda c, s: Capabilities(c, s, Extractor(c, httpx.MockTransport(handler)), Verifier()))


def model_response(request):
    body = json.loads(request.content)
    passage = json.loads(body['messages'][1]['content'])['passages'][0]
    assert body['store'] is False
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
        'requirements': [{'title': 'Login', 'description': 'Display login', 'criteria': ['Login is visible'], 'passage_id': passage['id']}]})}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})


async def test_extraction_is_grounded_reserved_and_idempotent(database):
    calls = []
    def handler(request):
        calls.append(request)
        with database() as db:
            assert db.scalar(select(CapabilityUsage)).status == 'reserved'
        return model_response(request)
    app = factory(database, handler)
    body = payload()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.post('/v1/imports', json=body)).status_code == 401
            response = await client.post('/v1/imports', json=body, headers=HEADERS)
            assert response.status_code == 200, response.text
            requirement = response.json()['requirements'][0]
            assert requirement['excerpt'] == 'The application shall display login.'
            assert requirement['char_start'] == 0 and requirement['char_end'] == len(requirement['excerpt'])
            assert (await client.post('/v1/imports', json=body, headers=HEADERS)).json() == response.json()
            changed = {**body, 'documents': [dict(d) for d in body['documents']]}
            changed['documents'][0]['filename'] = 'changed.txt'
            assert (await client.post('/v1/imports', json=changed, headers=HEADERS)).status_code == 409
    assert len(calls) == 1
    with database() as db:
        usage = db.scalar(select(CapabilityUsage))
        assert usage.status == 'succeeded' and usage.input_tokens == 10 and usage.amount is None


async def test_provider_failure_is_unknown_and_does_not_repeat_charge(database):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive provider detail', request=request)
    app = factory(database, handler)
    body = payload()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.post('/v1/imports', json=body, headers=HEADERS)).status_code == 503
            assert (await client.post('/v1/imports', json=body, headers=HEADERS)).status_code == 503
    assert len(calls) == 1
    with database() as db:
        assert db.scalar(select(CapabilityUsage)).amount is None
        assert db.scalar(select(CapabilityUsage)).status == 'unknown'


async def test_verification_does_not_persist_credentials(database):
    app = factory(database, model_response)
    body = {'request_id': str(uuid4()), 'environment_id': str(uuid4()), 'revision': 3,
            'target_url': 'https://example.com', 'credentials': {'password': 'never-store-password'}}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/v1/environments/verify', json=body, headers=HEADERS)
            assert response.status_code == 200 and response.json()['status'] == 'verified'
    with database() as db:
        row = db.scalar(select(CapabilityOperation))
        assert 'never-store-password' not in str(row.result) + row.fingerprint + str(row.error)
        import hashlib
        without_credentials = {key: value for key, value in body.items() if key != 'credentials'}
        expected = hashlib.sha256(json.dumps(without_credentials, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert row.fingerprint == expected


def test_binary_and_unsafe_uploads_rejected_without_model_calls():
    body = payload()
    body['documents'][0]['filename'] = '../brd.txt'
    with pytest.raises(CapabilityError): normalize(ExtractionRequest.model_validate(body).documents)
    body = payload()
    body['documents'][0]['content_base64'] = base64.b64encode(b'\x00binary').decode()
    with pytest.raises(CapabilityError): normalize(ExtractionRequest.model_validate(body).documents)


def test_docx_reader_uses_original_body_text():
    import io, zipfile
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Users shall sign in.</w:t></w:r></w:p></w:body></w:document>')
    body = payload()
    body['documents'][0].update(filename='brd.docx', content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document', content_base64=base64.b64encode(content.getvalue()).decode())
    blocks, _ = normalize(ExtractionRequest.model_validate(body).documents)
    assert blocks[0]['text'] == 'Users shall sign in.'


async def test_unknown_passage_is_rejected_without_invented_citation(database):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'requirements': [{'title': 'Invented', 'description': '', 'criteria': ['Unsupported'], 'passage_id': 'missing'}]})}}]})
    app = factory(database, handler)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.post('/v1/imports', json=payload(), headers=HEADERS)).status_code == 502


def test_pdf_parser_retains_selectable_text():
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 72 720 Td (Users shall sign in.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    body = payload()
    body['documents'][0].update(filename='brd.pdf', content_type='application/pdf', content_base64=base64.b64encode(buffer.getvalue()).decode())
    blocks, _ = normalize(ExtractionRequest.model_validate(body).documents)
    assert 'Users shall sign in.' in blocks[0]['text']
