import os
import tempfile
import unittest
from unittest.mock import patch
import multi_provider as r


class FreeRouterTests(unittest.TestCase):
    def test_disabled_preserves_legacy(self):
        with patch.dict(os.environ, {'HERMES_MULTI_PROVIDER_ENABLED':'false','GROQ_API_KEY':'configured'}):
            self.assertEqual(r.configured(), [])

    def test_no_paid_openrouter_model(self):
        env = {'HERMES_MULTI_PROVIDER_ENABLED':'true','HERMES_NEW_CLOUD_DATA_ALLOWED':'true',
               'OPENROUTER_API_KEY':'fake','OPENROUTER_MODEL':'anthropic/claude-sonnet'}
        with patch.dict(os.environ, env, clear=True):
            self.assertNotIn('openrouter', r.configured())
            os.environ['OPENROUTER_MODEL'] = 'vendor/test:free'
            self.assertIn('openrouter', r.configured())

    def test_new_cloud_requires_explicit_data_optin(self):
        env = {'HERMES_MULTI_PROVIDER_ENABLED':'true','OPENROUTER_API_KEY':'fake',
               'OPENROUTER_MODEL':'vendor/test:free'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(r.configured(), [])

    def test_route_fallback_uses_second_provider(self):
        env = {'HERMES_MULTI_PROVIDER_ENABLED':'true','HERMES_MULTI_PROVIDER_TASK_ROUTING':'true',
               'GROQ_API_KEY':'fake','GEMINI_API_KEY':'fake'}
        with patch.dict(os.environ, env, clear=True):
            called=[]
            def groq(ms,limit):
                called.append('groq')
                raise OSError('temporary')
            def gemini(ms,limit):
                called.append('gemini')
                return ('ok',False)
            result = r.complete([{'role':'user','content':'halo'}], lambda fn,ms:fn(ms)[0], groq, gemini)
            self.assertEqual(result, ('ok','gemini'))
            self.assertEqual(called, ['groq','gemini'])

    def test_budget_prevents_request_41(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'HERMES_PROVIDER_USAGE_FILE':folder+'/requests.json'}):
                self.assertTrue(all(r._claim_budget('openrouter') for _ in range(40)))
                self.assertFalse(r._claim_budget('openrouter'))

    def test_local_ollama_requires_optin_and_model(self):
        with patch.dict(os.environ, {'HERMES_MULTI_PROVIDER_ENABLED':'true',
                                     'HERMES_OLLAMA_ENABLED':'true','OLLAMA_MODEL':'llama3.2'}, clear=True):
            self.assertEqual(r.configured(), ['ollama'])

    def test_cloudflare_requires_free_plan_and_reviewed_model(self):
        env={'HERMES_MULTI_PROVIDER_ENABLED':'true','HERMES_NEW_CLOUD_DATA_ALLOWED':'true',
             'CLOUDFLARE_API_TOKEN':'fake','CLOUDFLARE_ACCOUNT_ID':'a'*32,
             'CLOUDFLARE_AI_MODEL':'@cf/meta/llama-3.1-8b-instruct'}
        with patch.dict(os.environ, env, clear=True):
            self.assertNotIn('cloudflare',r.configured())
            os.environ['CLOUDFLARE_FREE_PLAN_CONFIRMED']='true'
            self.assertIn('cloudflare',r.configured())
            os.environ['CLOUDFLARE_AI_MODEL']='@cf/deepseek-ai/deepseek-v4-pro-0813'
            self.assertNotIn('cloudflare',r.configured())

    def test_task_type_deterministic(self):
        self.assertEqual(r.task_type([{'role':'user','content':'Tolong audit SQL dan patch React'}]),'coding')
        self.assertEqual(r.task_type([{'role':'user','content':'Monitoring jaringan DNS'}]),'operations')


if __name__=='__main__':
    unittest.main()
