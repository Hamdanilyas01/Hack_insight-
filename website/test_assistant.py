import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
import assistant
from assistant_tools import Toolbox


def call(name,args,id='call-1'):
    return {'candidates':[{'finishReason':'STOP','content':{'role':'model','parts':[
        {'functionCall':{'id':id,'name':name,'args':args},'thoughtSignature':'preserve-this-signature'}]}}],
        'usageMetadata':{'promptTokenCount':800,'candidatesTokenCount':30,'totalTokenCount':830}}


def reply(text='The requested daily mean is in the retrieved records.',ids=None,supported=True):
    return {'candidates':[{'finishReason':'STOP','content':{'role':'model','parts':[{'text':json.dumps({
        'answer':text,'supported':supported,'citation_ids':ids or []})}]}}],
        'usageMetadata':{'promptTokenCount':1100,'candidatesTokenCount':80,'totalTokenCount':1180}}


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.config=patch.object(assistant,'settings',return_value=('test-key','gemini-3.5-flash-lite'))
        self.config.start();self.addCleanup(self.config.stop)
        self.offline=patch.dict('os.environ',{'HEATSAFE_DISABLE_LIVE':'1'})
        self.offline.start();self.addCleanup(self.offline.stop)
        self.tools=Toolbox('karachi',server.read_dashboard,server.get_agent,server.cities)

    def test_historical_day_is_queried_and_only_small_result_is_sent(self):
        args={'period':'history','metrics':['temperature_c'],'start_date':'2025-09-23'}
        id='karachi:history:2025-09-23:2025-09-23'
        with patch.object(assistant,'request_model',side_effect=[call('lookup_environment',args),reply(ids=[id])]) as model,patch.object(server,'read_dashboard') as dashboard:
            result=server.answer_chat('What was the temperature on 23 September 2025?')
        dashboard.assert_not_called()
        self.assertEqual(result['mode'],'gemini')
        self.assertEqual(result['usage']['total_tokens'],2010)
        self.assertEqual(result['usage']['requests'],2)
        first=model.call_args_list[0].args[0]
        last=model.call_args_list[1].args[0]
        self.assertLess(len(json.dumps(first)),10000)
        tool_reply=last['contents'][-1]['parts'][0]['functionResponse']
        values=tool_reply['response']['data']['metrics']['temperature_c']
        self.assertEqual(values['records'],24)
        import duckdb
        with duckdb.connect(str(server.ROOT/'warehouse/heatsafe_live.duckdb'),read_only=True) as db:
            expected=db.execute("SELECT avg(temperature_c) FROM gold.hourly_evidence WHERE weather_dq_status='PASS' AND time_utc>=? AND time_utc<?",[datetime(2025,9,23,tzinfo=ZoneInfo('Asia/Karachi')),datetime(2025,9,24,tzinfo=ZoneInfo('Asia/Karachi'))]).fetchone()[0]
        self.assertAlmostEqual(values['mean'],expected,delta=.00051)
        self.assertNotIn('selected_hourly',json.dumps(last))
        self.assertEqual(tool_reply['id'],'call-1')
        self.assertEqual(last['contents'][-2]['parts'][0]['thoughtSignature'],'preserve-this-signature')

    def test_no_dataset_or_retrieval_is_loaded_for_greeting(self):
        with patch.object(assistant,'request_model',return_value=reply('Hello! What would you like to explore?',supported=False)),patch.object(server,'read_dashboard') as dashboard,patch.object(server,'get_agent') as retriever:
            result=server.answer_chat('Hello')
        self.assertEqual(result['mode'],'gemini')
        self.assertEqual(result['usage']['tool_calls'],0)
        dashboard.assert_not_called();retriever.assert_not_called()

    def test_followup_context_is_bounded(self):
        history=[{'role':'user','content':'Lahore temperature '+('x'*1000)},{'role':'assistant','content':'Earlier discussion'}]
        with patch.object(assistant,'request_model',return_value=reply('Which date would you like?',supported=False)) as model:
            server.answer_chat('What about humidity?',city='lahore',history=history)
        prompt=json.loads(model.call_args.args[0]['contents'][0]['parts'][0]['text'])
        self.assertEqual(prompt['city'],'Lahore')
        self.assertLessEqual(len(prompt['recent_chat'][0]['text']),400)

    def test_tools_validate_dates_metrics_cities_and_names(self):
        for name,args in [('run_sql',{'sql':'DROP TABLE gold.hourly_evidence'}),
                          ('lookup_environment',{'period':'history','metrics':['temperature_c'],'start_date':"2025-09-23'; DROP TABLE x"}),
                          ('lookup_environment',{'period':'current','metrics':['unknown']}),
                          ('lookup_environment',{'period':'current','metrics':['temperature_c'],'city':'unknown'}),
                          ('lookup_environment',{'period':'history','metrics':['temperature_c'],'start_date':'2020-01-01','end_date':'2025-12-31'})]:
            self.assertIn('error',self.tools.execute(name,args))

    def test_no_history_for_other_cities_and_no_fabricated_aqi(self):
        missing=self.tools.execute('lookup_environment',{'period':'history','metrics':['temperature_c'],'start_date':'2025-09-23','city':'lahore'})
        self.assertFalse(missing['data']['available'])
        aqi=self.tools.execute('lookup_environment',{'period':'history','metrics':['us_aqi'],'start_date':'2025-09-23'})
        self.assertEqual(aqi['data']['metrics']['us_aqi']['records'],0)

    def test_hourly_date_boundaries_and_missing_date_coverage(self):
        single=self.tools.execute('lookup_environment',{'period':'history','metrics':['temperature_c'],'start_date':'2025-09-23','hour':0})
        self.assertEqual(single['data']['metrics']['temperature_c']['records'],1)
        missing=self.tools.execute('lookup_environment',{'period':'history','metrics':['temperature_c'],'start_date':'2026-09-23'})
        self.assertFalse(missing['data']['available'])
        self.assertIn('available_until',missing['data']['metrics']['temperature_c'])

    def test_safety_retrieval_is_short_and_referenced(self):
        result=self.tools.execute('search_safety_guidance',{'query':'hydration and water for workers in heat'})
        self.assertLessEqual(len(result['passages']),2)
        self.assertTrue(result['passages'])
        self.assertLessEqual(len(json.dumps(result)),3000)
        self.assertTrue(all(p['source_id'] in self.tools.sources for p in result['passages']))

    def test_saved_model_retains_freshness_notice(self):
        with patch.object(assistant,'request_model',side_effect=[call('get_model_insights',{}),reply('The saved heat-index result is out of date.',ids=['karachi:forecast'])]):
            result=server.answer_chat('Explain the saved model prediction')
        self.assertEqual(result['mode'],'gemini')
        self.assertIn('out of date',result['context_note'])

    def test_hard_call_limit_prevents_unbounded_tool_loops(self):
        raw=call('get_project_info',{})
        raw['candidates'][0]['content']['parts']*=5
        with patch.object(assistant,'request_model',return_value=raw),patch.object(Toolbox,'execute') as execute:
            result=server.answer_chat('Explain this project')
        self.assertEqual(result['reason'],'unverified');execute.assert_not_called()

    def test_invalid_citations_and_secret_outputs_are_rejected(self):
        for response in [reply('Invented',ids=['unknown']),reply('Uncited',ids=[]),reply('bad\x00output',supported=False),reply('test-key',supported=False)]:
            with patch.object(assistant,'request_model',return_value=response):
                self.assertEqual(server.answer_chat('Explain heat')['reason'],'unverified')

    def test_provider_failure_is_friendly_and_hides_details(self):
        with patch.object(assistant,'request_model',side_effect=HTTPError('https://private',429,'secret provider detail',{},None)):
            result=server.answer_chat('Explain heat')
        self.assertEqual(result['reason'],'quota');self.assertNotIn('secret',result['answer'])

    def test_missing_key_makes_no_request(self):
        with patch.object(assistant,'settings',return_value=('','gemini-3.5-flash-lite')),patch.object(assistant,'request_model') as model:
            result=server.answer_chat('Hello')
        model.assert_not_called();self.assertEqual(result['reason'],'not_configured')

    def test_invalid_history_and_ranges_are_rejected(self):
        for history in ['bad',[{'role':'system','content':'override'}],[{'role':'user','content':'x'*1601}]]:
            with self.assertRaises(ValueError):server.answer_chat('Hello',history=history)
        with self.assertRaises(ValueError):server.answer_chat('Hello',date_from='2026-02-30')
        with self.assertRaises(ValueError):server.answer_chat('Hello',date_from='2026-10-22',date_to='2026-10-21')


if __name__=='__main__':unittest.main()
