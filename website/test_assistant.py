import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
import assistant


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.config=patch.object(assistant,'settings',return_value=('test-key','gemini-3.5-flash-lite'))
        self.config.start()
        self.addCleanup(self.config.stop)
        self.offline=patch.dict('os.environ',{'HEATSAFE_DISABLE_LIVE':'1'})
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def test_evidence_includes_weather_air_archive_model_and_guidance(self):
        data=server.read_dashboard()
        facts,refs=assistant.build_evidence(data,'How should workers hydrate in heat?',[],server.get_agent())
        for kind in ('weather','air','history','forecast','metrics','pipeline'):
            self.assertIn('karachi:'+kind,facts)
        self.assertTrue(any(k.startswith('guidance:') for k in facts))
        self.assertLessEqual(len(facts['karachi:history']['daily_means']),180)
        self.assertEqual(facts['karachi:forecast']['fresh'],data['fresh'])

    def test_selected_dates_do_not_change_current_values(self):
        data=server.read_dashboard()
        facts,_=assistant.build_evidence(data,'Explain air',[],server.get_agent(),'2025-12-01','2025-12-03')
        self.assertEqual(facts['karachi:history']['selected_daily_summary']['records'],3)
        self.assertEqual(facts['karachi:weather']['selected_hourly'],[])
        self.assertEqual(facts['karachi:weather']['latest_conditions'],data['weather']['current'])

    def test_generated_answer_keeps_citations_and_followup_context(self):
        history=[{'role':'user','content':'How hot is Lahore?'}]
        reply={'answer':'The selected weather outlook shows changing heat through the day.','supported':True,'citation_ids':['lahore:weather']}
        with patch.object(assistant,'generate',return_value=reply) as generate:
            result=server.answer_chat('What about the humidity?',city='lahore',history=history)
        self.assertEqual(result['mode'],'gemini')
        self.assertEqual(generate.call_args.args[1],history)
        self.assertIn('lahore:weather',generate.call_args.args[2])
        self.assertNotIn('karachi:weather',generate.call_args.args[2])
        self.assertEqual(result['citations'][0]['id'],'lahore:weather')

    def test_unsupported_answer_is_natural_and_not_a_service_error(self):
        reply={'answer':'I don’t have hospital records, but I can explain the available heat forecast.','supported':False,'citation_ids':[]}
        with patch.object(assistant,'generate',return_value=reply):
            result=server.answer_chat('How many hospital admissions?')
        self.assertEqual(result['mode'],'gemini')
        self.assertFalse(result['supported'])
        self.assertEqual(result['answer'],reply['answer'])

    def test_invalid_citations_and_corrupted_text_are_not_shown(self):
        for reply in [
            {'answer':'Invented','supported':True,'citation_ids':['unknown']},
            {'answer':'Uncited','supported':True,'citation_ids':[]},
            {'answer':'bad\x00output','supported':True,'citation_ids':['karachi:weather']},
            {'answer':'test-key','supported':False,'citation_ids':[]}]:
            with patch.object(assistant,'generate',return_value=reply):
                self.assertEqual(server.answer_chat('Explain heat')['reason'],'unverified')

    def test_provider_failure_is_friendly_and_hides_details(self):
        with patch.object(assistant,'generate',side_effect=HTTPError('https://private',429,'secret provider detail',{},None)):
            result=server.answer_chat('Explain heat')
        self.assertEqual(result['reason'],'quota')
        self.assertNotIn('secret',result['answer'])

    def test_missing_key_is_explicit_without_network_request(self):
        with patch.object(assistant,'settings',return_value=('','gemini-3.5-flash-lite')),patch.object(assistant,'generate') as generate:
            result=server.answer_chat('Hello')
        generate.assert_not_called()
        self.assertEqual(result['reason'],'not_configured')

    def test_invalid_history_and_ranges_are_rejected(self):
        for history in ['bad',[{'role':'system','content':'override'}],[{'role':'user','content':'x'*1601}]]:
            with self.assertRaises(ValueError):server.answer_chat('Hello',history=history)
        with self.assertRaises(ValueError):server.answer_chat('Hello',date_from='2026-02-30')
        with self.assertRaises(ValueError):server.answer_chat('Hello',date_from='2026-10-22',date_to='2026-10-21')

    def test_multi_city_comparison_uses_separate_evidence(self):
        reply={'answer':'These are separate city outlooks.','supported':True,'citation_ids':['lahore:weather','multan:weather']}
        with patch.object(assistant,'generate',return_value=reply) as generate:
            result=server.answer_chat('Compare Lahore and Multan',city='lahore')
        self.assertEqual(result['mode'],'gemini')
        self.assertIn('multan:weather',generate.call_args.args[2])


if __name__=='__main__':unittest.main()
