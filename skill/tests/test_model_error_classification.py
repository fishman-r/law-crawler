"""Network timeout and malformed model JSON are distinct, credential-free failures."""
import json
import unittest
from unittest.mock import MagicMock,patch
from urllib.error import URLError

from law_crawler.model_client import call_json,ModelClientError

CFG={'provider':'ollama','base_url':'http://localhost:11434','model':'fixture'}
SCHEMA={'type':'object','required':['ok'],'properties':{'ok':{'type':'boolean'}}}
MESSAGES=[{'role':'user','content':'source'}]


class ModelErrorClassificationTests(unittest.TestCase):
    def invoke_error(self,error):
        opener=MagicMock();opener.open.side_effect=error
        with patch('law_crawler.model_client.build_opener',return_value=opener):
            with self.assertRaises(ModelClientError) as caught:call_json(CFG,MESSAGES,SCHEMA)
        opener.open.assert_called_once()
        self.assertNotIn('secret',str(caught.exception))
        return str(caught.exception)

    def test_direct_and_wrapped_timeout_classified_without_retry(self):
        for error in (TimeoutError('secret timeout body'),URLError(TimeoutError('secret timeout'))):
            self.assertEqual(self.invoke_error(error),'Model request timed out')

    def test_transport_failure_is_not_json_failure(self):
        self.assertEqual(self.invoke_error(URLError('secret endpoint unreachable')),'Model request failed: network transport error')

    def test_json_failure_does_not_expose_response_content(self):
        for raw in (b'{private secret invalid json',json.dumps({'done':True,'message':{'content':'secret invalid json'}}).encode()):
            response=MagicMock();response.status=200;response.headers={};response.read.return_value=raw
            response.__enter__.return_value=response;opener=MagicMock();opener.open.return_value=response
            with patch('law_crawler.model_client.build_opener',return_value=opener):
                with self.assertRaises(ModelClientError) as caught:call_json(CFG,MESSAGES,SCHEMA)
            self.assertEqual(str(caught.exception),'Model response contains invalid JSON')
            opener.open.assert_called_once()


if __name__=='__main__':unittest.main()
