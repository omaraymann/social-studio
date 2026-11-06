"""Image-analysis contract tests; no paid provider requests."""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.llm.base import LLMError
from app.llm.image_analysis import analysis_payload, parse_analysis
from integrations.vision import analyse


def body():
    return {'id': 'test-response', 'usage': {'prompt_tokens': 100, 'completion_tokens': 120, 'cost': 0.000058},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'scene_description': 'A pale cup and a dark teapot on a table.',
                'topics': ['tea', 'tableware'],
                'objects': [{'label': 'cup', 'description': 'A pale handled cup.',
                             'attributes': ['pale', 'handled'], 'box_2d': [200, 100, 700, 400], 'uncertainty': ''}],
                'uncertainties': ['Manufacturer and capacity are unknown.'],
            })}}]}


def test_payload_is_image_only_and_capped():
    payload = analysis_payload('data:image/png;base64,TEST')
    assert len(payload['messages']) == 2
    user = payload['messages'][1]['content']
    assert user[1] == {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,TEST'}}
    assert len(user) == 2
    assert payload['max_tokens'] == 4096 and payload['temperature'] == 0
    assert payload['reasoning'] == {'enabled': False}
    assert payload['response_format']['json_schema']['strict'] is True


@pytest.mark.parametrize('box', [[0, 1, 0, 50], [-1, 0, 20, 30], [0, 0, 1001, 50],
                                  [700, 10, 200, 50], [True, 0, 30, 50], [0.3, 0, 30, 50]])
def test_invalid_boxes_are_rejected(box):
    response = body()
    content = json.loads(response['choices'][0]['message']['content'])
    content['objects'][0]['box_2d'] = box
    response['choices'][0]['message']['content'] = json.dumps(content)
    with pytest.raises(LLMError, match='validation'):
        parse_analysis(response)


def test_truncated_response_is_rejected_even_if_json_is_valid():
    response = body()
    response['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(LLMError):
        parse_analysis(response)


def test_orientation_and_box_conversion(tmp_path):
    path = tmp_path / 'rotated.jpg'
    image = Image.new('RGB', (100, 50), 'blue')
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)
    prepared, raw, data_url = analyse.prepare_image(path)
    assert prepared.size == (50, 100)
    assert not prepared.getexif()
    assert data_url.startswith('data:image/png;base64,')
    assert analyse.pixel_box([200, 100, 700, 400], 1000, 500) == [100, 100, 400, 350]
    assert analyse.pixel_box([0, 0, 1000, 1000], 1000, 500) == [0, 0, 1000, 500]


class FakeConnection:
    def __init__(self):
        self.record = None
    def execute(self, sql, params=()):
        if sql.startswith('SELECT status'):
            found = (self.record['status'], self.record['artifact_directory']) if self.record else None
            return SimpleNamespace(fetchone=lambda: found)
        if 'INSERT INTO rag.image_analyses' in sql:
            self.record = {'status': 'processing', 'artifact_directory': params[7]}
        elif "SET status='succeeded'" in sql:
            self.record['status'] = 'succeeded'
        elif "SET status='failed'" in sql:
            self.record['status'] = 'failed'
        return SimpleNamespace(rowcount=1)
    def commit(self):
        pass
    def rollback(self):
        pass


def source(tmp_path):
    path = tmp_path / 'source.png'
    Image.new('RGB', (60, 40), 'white').save(path)
    return {'file_id': 'file-one', 'name': 'opaque.png', 'cache_path': str(path),
            'content_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_success_is_persisted_and_reused_without_another_paid_call(tmp_path, monkeypatch):
    monkeypatch.setattr(analyse, 'ANALYSIS_DIRECTORY', tmp_path / 'results')
    connection, calls = FakeConnection(), []
    async def requester(image, model):
        calls.append(1)
        return body()
    row = source(tmp_path)
    first = asyncio.run(analyse.process_image(connection, 1, row, 'test-model', requester=requester))
    second = asyncio.run(analyse.process_image(connection, 1, row, 'test-model', requester=requester))
    assert len(calls) == 1
    assert first['status'] == 'succeeded' and second['status'] == 'cached'
    assert first['cost_usd'] == 0.000058
    from pathlib import Path
    folder = Path(first['artifact_directory'])
    assert (folder / 'boxes.png').is_file() and (folder / 'analysis.json').is_file()
    assert 'data:image' not in (folder / 'provenance.json').read_text()


def test_failed_result_is_recorded_and_never_automatically_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(analyse, 'ANALYSIS_DIRECTORY', tmp_path / 'results')
    connection, calls = FakeConnection(), []
    async def requester(image, model):
        calls.append(1)
        response = body()
        response['choices'][0]['message']['content'] = 'not-json'
        return response
    row = source(tmp_path)
    with pytest.raises(LLMError):
        asyncio.run(analyse.process_image(connection, 1, row, 'test-model', requester=requester))
    assert connection.record['status'] == 'failed'
    with pytest.raises(ValueError, match='already exists'):
        asyncio.run(analyse.process_image(connection, 1, row, 'test-model', requester=requester))
    assert len(calls) == 1


def test_error_diagnostic_keeps_http_status_and_redacts_sensitive_data(monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, 'openrouter_api_key', 'test-secret-do-not-retain')
    request = httpx.Request('POST', 'https://openrouter.ai/api/v1/chat/completions',
                            headers={'Authorization': 'Bearer test-secret-do-not-retain'})
    response = httpx.Response(429, request=request, json={'request_id': 'test-request', 'error': {'code': 429}})
    cause = httpx.HTTPStatusError('provider error', request=request, response=response)
    exc = LLMError('OpenRouter rate limit reached')
    exc.__cause__ = cause
    diagnostic = analyse.error_diagnostic(exc)
    assert diagnostic['http_status'] == 429 and diagnostic['request_id'] == 'test-request'
    assert 'test-secret-do-not-retain' not in json.dumps(diagnostic)
    assert analyse.error_diagnostic(LLMError('Bearer test-secret-do-not-retain'))['message'] == '[redacted]'


def test_prompt_versions_have_separate_cache_keys_and_do_not_change_baseline():
    from app.llm.image_analysis import PROMPT_VERSION, REVISED_PROMPT_VERSION, ANALYSIS_PROMPT, REVISED_ANALYSIS_PROMPT
    row={'file_id':'image','content_sha256':'hash'}
    assert analyse.analysis_key(1,row,'model',PROMPT_VERSION)!=analyse.analysis_key(1,row,'model',REVISED_PROMPT_VERSION)
    original=analysis_payload('data:image/png;base64,TEST')
    revised=analysis_payload('data:image/png;base64,TEST',prompt_version=REVISED_PROMPT_VERSION)
    assert original['messages'][0]['content']==ANALYSIS_PROMPT
    assert revised['messages'][0]['content']==REVISED_ANALYSIS_PROMPT
    assert original['response_format']['json_schema']['schema']['properties']['objects']['maxItems']==16
    assert revised['response_format']['json_schema']['schema']['properties']['objects']['maxItems']==12


@pytest.mark.parametrize('rate', ['nan', '-1', '0.0000026'])
def test_model_comparison_price_guard_rejects_invalid_or_excessive_rates(rate,monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url):
            data={'data':[{'id':'google/gemini-2.5-flash','architecture':{'input_modalities':['image']},
                          'supported_parameters':['structured_outputs','response_format'],
                          'pricing':{'prompt':'0.0000003','completion':rate}}]}
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(analyse.httpx,'AsyncClient',lambda **kw:Client())
    with pytest.raises(ValueError,match='guard'):
        asyncio.run(analyse.model_price('google/gemini-2.5-flash'))
