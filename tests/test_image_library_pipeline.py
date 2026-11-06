"""Integration contracts; provider responses are mocked and never billable."""
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.llm.base import GenerationSettings, LLMResult
from app.llm.openrouter import OpenRouterProvider
from app.llm.registry import get_llm_provider
from app.main import app
from app.services import campaign_visuals, image_selection
from integrations.image_retrieval import library


def png(color):
    out=io.BytesIO();Image.new('RGB',(100,100),color).save(out,'PNG');return out.getvalue()


def plans():
    return {'x':{'headline':'A reading corner','call_to_action':'Learn more',
                 'visual_brief':'A welcoming reading corner using the selected scene reference. No lettering.'}}


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'image_storage_dir',str(tmp_path/'campaigns'))
    monkeypatch.setattr(settings,'openrouter_api_key','test-key')
    monkeypatch.setattr(library,'UPLOAD_ROOT',tmp_path/'library')
    library.UPLOAD_ROOT.mkdir()
    candidates=[]
    for i,color in enumerate(['blue','green'],1):
        raw=png(color);path=library.UPLOAD_ROOT/f'{i}.png';path.write_bytes(raw)
        candidates.append({'id':i,'owner_id':1,'source_id':f'upload:{i}','filename':f'photo-{i}.png',
                           'path':str(path),'source_sha256':hashlib.sha256(raw).hexdigest(),
                           'metadata':{'reference_role':'scene'},'similarity':0.2/i})
    monkeypatch.setattr(library,'listing',lambda owner:{'ready':2})
    monkeypatch.setattr(library,'search',lambda owner,query,k:candidates)
    state={'selected':[{'image_id':1,'reason':'The reading setting fits the campaign.','adaptation_notes':'Brighten the scene and simplify the desk background.'}],
           'image_calls':0,'selection_calls':0,'candidates':candidates}
    async def select_request(path,payload,transport=None):
        state['selection_calls']+=1
        assert path=='chat/completions'
        assert len([c for c in payload['messages'][1]['content'] if c['type']=='image_url'])==2
        context=json.loads(payload['messages'][1]['content'][0]['text'])
        state['selector_context']=context
        result={'selected':state['selected'],'no_match_reason':state.get('no_match_reason', '' if state['selected'] else 'No relevant scene.'), 'visuals':plans()}
        return {'choices':[{'message':{'content':json.dumps(result)}}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'cost':0.001}}
    monkeypatch.setattr(image_selection,'request_openrouter',select_request)
    async def image_request(model,prompt,images,ratio):
        state['image_calls']+=1
        state['images']=images;state['prompt']=prompt
        return png('yellow'),{'cost':0.01}
    monkeypatch.setattr(campaign_visuals,'generate_image',image_request)
    class Provider:
        name='fake'
        async def generate(self,**kwargs):
            assert kwargs.get('image_search') is True
            return LLMResult(variants={'x':'Explore a comfortable reading corner. Learn more'},
                             visual_briefs=plans(),image_search_query='A comfortable reading corner',
                             input_tokens=10,output_tokens=5,cost_usd=0.001)
    app.dependency_overrides[get_llm_provider]=lambda:Provider()
    yield state
    app.dependency_overrides.clear()


def generate(client,headers,**options):
    post=client.post('/posts',headers=headers,json={'title':'Reading spaces',
        'markdown':'Explore ideas for creating a comfortable reading space at home.'}).json()
    response=client.post(f'/posts/{post["id"]}/variants',headers=headers,json={
        'platforms':['x'],'generation_mode':'llm','call_to_action':'Learn more',
        'generate_images':True,'auto_select_images':True,**options})
    return post['id'],response


def test_automatic_pipeline_passes_only_selected_pixels_to_generator(client,auth_headers,pipeline):
    post_id,response=generate(client,auth_headers)
    assert response.status_code==201,response.text
    visuals=client.get(f'/posts/{post_id}/visuals',headers=auth_headers).json()
    assert visuals[0]['status']=='succeeded'
    audit=visuals[0]['context']['image_retrieval']
    assert audit['query']=='A comfortable reading corner'
    assert len(audit['candidates'])==2 and len(audit['selected'])==1
    assert len(visuals[0]['reference_image_ids'])==1
    assert pipeline['selection_calls']==1 and pipeline['image_calls']==1
    assert len(pipeline['images'])==1
    assert 'scene and composition references only' in pipeline['prompt']
    assert 'product\'s shape' not in pipeline['prompt']
    assert 'Brighten the scene and simplify the desk background.' in pipeline['prompt']
    assert 'Suggested reference adaptations' in pipeline['prompt']
    runs=client.get(f'/posts/{post_id}/generations',headers=auth_headers).json()
    assert runs[0]['input_tokens']==30 and runs[0]['output_tokens']==15
    assert runs[0]['estimated_cost_usd']==pytest.approx(0.002)


def test_rejected_candidates_stop_paid_image_call(client,auth_headers,pipeline):
    pipeline['selected']=[]
    post_id,response=generate(client,auth_headers)
    assert response.status_code==201,response.text
    visuals=client.get(f'/posts/{post_id}/visuals',headers=auth_headers).json()
    assert visuals[0]['status']=='failed'
    assert 'No suitable library photo' in visuals[0]['error_message']
    assert pipeline['image_calls']==0


def test_selected_photos_allow_null_inapplicable_no_match_reason(client,auth_headers,pipeline):
    pipeline['no_match_reason']=None
    post_id,response=generate(client,auth_headers)
    assert response.status_code==201,response.text
    visual=client.get(f'/posts/{post_id}/visuals',headers=auth_headers).json()[0]
    assert visual['status']=='succeeded'
    assert visual['context']['image_retrieval']['no_match_reason']==''
    assert pipeline['image_calls']==1


@pytest.mark.parametrize('reason',[None,'','   '])
def test_no_selection_still_requires_real_explanation(client,auth_headers,pipeline,reason):
    pipeline['selected']=[]
    pipeline['no_match_reason']=reason
    _,response=generate(client,auth_headers)
    assert response.status_code==502
    assert pipeline['image_calls']==0


def test_explicit_illustration_policy_allows_zero_references(client,auth_headers,pipeline):
    pipeline['selected']=[]
    post_id,response=generate(client,auth_headers,image_no_match_policy='illustration')
    assert response.status_code==201,response.text
    assert pipeline['image_calls']==1 and pipeline['images']==[]
    assert 'Create a topic illustration' in pipeline['prompt']


def test_unknown_selected_id_fails_without_generation(client,auth_headers,pipeline):
    pipeline['selected']=[{'image_id':999,'reason':'Not in candidate pool','adaptation_notes':None}]
    post_id,response=generate(client,auth_headers)
    assert response.status_code==502
    assert pipeline['image_calls']==0
    assert client.get(f'/posts/{post_id}/variants',headers=auth_headers).json()==[]


def test_auto_mode_rejects_manual_reference_mix(client,auth_headers,pipeline):
    _,response=generate(client,auth_headers,reference_image_ids=[1])
    assert response.status_code==422
    assert pipeline['selection_calls']==0


def test_empty_library_stops_before_llm(client,auth_headers,pipeline,monkeypatch):
    monkeypatch.setattr(library,'listing',lambda owner:{'ready':0})
    _,response=generate(client,auth_headers)
    assert response.status_code==502
    assert pipeline['selection_calls']==0 and pipeline['image_calls']==0


def test_generated_visual_details_are_not_selector_requirements(client,auth_headers,pipeline):
    class InventiveProvider:
        name='fake'
        async def generate(self,**kwargs):
            provisional=plans()
            provisional['x']['visual_brief']='Must have natural light, a window and an ergonomic chair.'
            return LLMResult(variants={'x':'Explore a comfortable reading corner. Learn more'},
                             visual_briefs=provisional,
                             image_search_query='A comfortable reading corner with natural light')
    app.dependency_overrides[get_llm_provider]=lambda:InventiveProvider()
    _,response=generate(client,auth_headers)
    assert response.status_code==201,response.text
    context=pipeline['selector_context']
    assert 'plans' not in context and 'image_search_query' not in context
    assert 'natural light' not in json.dumps(context)
    assert context['original_campaign']['design_instructions']==''
    assert 'reading space' in context['original_campaign']['source']


def test_explicit_user_visual_requirements_are_kept(client,auth_headers,pipeline):
    _,response=generate(client,auth_headers,image_instructions='The reference must show a window and natural light.')
    assert response.status_code==201,response.text
    assert pipeline['selector_context']['original_campaign']['design_instructions']=='The reference must show a window and natural light.'


def test_changed_source_is_not_sent_to_selector(pipeline):
    row=pipeline['candidates'][0]
    from pathlib import Path
    Path(row['path']).write_bytes(png('red'))
    with pytest.raises(ValueError,match='changed'):
        library.read_asset(row)


def test_structured_campaign_query_is_parsed_separately():
    async def exercise():
        def respond(request):
            payload=json.loads(request.content)
            assert 'image_search_query' in payload['response_format']['json_schema']['schema']['required']
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
                'x':'A caption','image_search_query':'A desk by a window'})}}]})
        provider=OpenRouterProvider('test-key','https://example.test',5,transport=httpx.MockTransport(respond))
        result=await provider.generate(system_prompt='system',user_prompt='brief',platforms=['x'],image_search=True,
                                      settings=GenerationSettings('test',0,1,None,200))
        assert result.image_search_query=='A desk by a window'
        assert result.variants=={'x':'A caption'}
    import asyncio
    asyncio.run(exercise())


def test_scene_role_survives_manual_regeneration(client,auth_headers,pipeline,monkeypatch):
    post_id,response=generate(client,auth_headers)
    assert response.status_code==201
    first=client.get(f'/posts/{post_id}/visuals',headers=auth_headers).json()[0]
    async def plan(model,context,images,**kwargs):
        assert kwargs['scene_references'] is True
        assert json.loads(context)['reference_roles']==['scene']
        return plans()['x'],{'cost':0.001}
    monkeypatch.setattr(campaign_visuals,'create_visual_brief',plan)
    response=client.post(f'/posts/{post_id}/visuals',headers=auth_headers,json={
        'platform':'x','reference_image_ids':first['reference_image_ids']})
    assert response.status_code==202,response.text
    latest=client.get(f'/posts/{post_id}/visuals',headers=auth_headers).json()[0]
    assert latest['status']=='succeeded'
    assert 'scene and composition references only' in pipeline['prompt']
    assert pipeline['selection_calls']==1
