import json,pathlib,sqlite3,sys,tempfile,threading,unittest,urllib.request,urllib.error,io
from urllib.parse import quote
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'数仓'))
import db,db_eco,server,seed_eco
from openpyxl import load_workbook

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.old=db.DB_PATH;self.upload=server.UPLOAD_DIR
        db.DB_PATH=str(pathlib.Path(self.tmp.name)/'warehouse.db')
        seed_eco.seed_defaults()
        # Artificial rows exist only in this temporary database, never in the repository.
        db_eco.dict_merge(entities=[{'name':'测试主体','segment':'测试板块','region':'境内','currency':'CNY','account':'TEST-ACCOUNT'}])
        db_eco.dict_replace(fx=[{'currency':'USD','name':'测试币种','rate_cny':7}])
        seed_eco.ensure_fx_params()
        server.UPLOAD_DIR=str(pathlib.Path(self.tmp.name)/'uploads')
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        db.DB_PATH=self.old;server.UPLOAD_DIR=self.upload;self.tmp.cleanup()
    def call(self,path,body=None,raw=None,headers=None):
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.base+path,data=data,headers=headers or {'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:
                out=r.read();return r.status,json.loads(out) if 'json' in r.headers.get('Content-Type','') else out
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def test_manual_roundtrip_and_bad_request(self):
        k='2026-08-01|33.3|测试接口'
        self.assertTrue(self.call('/api/manual',{'manual':{k:{'l1':'管理费用','l2':'测试','cf':'经营活动'}}})[1]['ok'])
        self.assertEqual(self.call('/api/rows')[1]['manual'][k]['l2'],'测试')
        self.assertEqual(self.call('/api/manual',{'manual':{'bad|bad|bad':{}}})[0],400)
    def test_default_template_download_is_valid_and_empty(self):
        code,data=self.call('/api/template?id=TPL-05');self.assertEqual(code,200)
        wb=load_workbook(io.BytesIO(data));self.assertIn('预算明细',wb.sheetnames);self.assertIn('科目字典',wb.sheetnames)
        self.assertIsNone(wb['预算明细']['I4'].value);wb.close()
    def test_downloaded_template_upload_uses_saved_exchange_rate(self):
        data=self.call('/api/template?id=TPL-05')[1];wb=load_workbook(io.BytesIO(data))
        config=self.call('/api/eco')[1];entity=config['entities'][0]
        subject=next(s for s in config['subjects'] if s['direction']=='流入')
        values=[1,entity['name'],entity['segment'],entity['region'],'USD','流入',subject['cat'],subject['subject'],100,None,'上旬','回归测试','草稿','模板回传测试']
        for col,value in enumerate(values,1):wb['预算明细'].cell(4,col,value)
        raw=io.BytesIO();wb.save(raw);wb.close()
        self.call('/api/params',{'params':{'fx.USD':'8.25'}})
        code,d=self.call('/api/upload-plan',raw=raw.getvalue(),headers={'Content-Type':'application/octet-stream','X-File-Name':'template_roundtrip.xlsx','X-Period':'2027-01'})
        self.assertEqual(code,200,d)
        detail=self.call('/api/submission?id='+str(d['sub_id']))[1]
        self.assertEqual(detail['submission']['items'][0]['amt_cny'],825)
        after=next(e for e in self.call('/api/eco')[1]['entities'] if e['name']==entity['name'])
        self.assertEqual(after['account'],entity['account'])
    def test_parameters_and_terms_roundtrip(self):
        self.assertTrue(self.call('/api/params',{'params':{'safe.line':'1234567'},'actor':'回归测试'})[1]['ok'])
        self.assertTrue(self.call('/api/terms',{'rows':[{'customer':'回归测试客户','term_days':0}]})[1]['ok'])
        data=self.call('/api/eco')[1]
        self.assertEqual(next(x['value'] for x in data['params'] if x['id']=='safe.line'),'1234567')
        self.assertEqual(next(x['term_days'] for x in data['terms'] if x['customer']=='回归测试客户'),0)
    def test_upload_approve_reload_and_settle_keep_plan(self):
        wb=load_workbook(io.BytesIO(self.call('/api/template?id=TPL-05')[1]))
        values=[1,'测试主体','测试板块','境内','CNY','流入','经营活动','销售回款（商品/渠道分销）',100,None,'上旬','测试人员','草稿','自动生成的测试记录']
        for col,value in enumerate(values,1):wb['预算明细'].cell(4,col,value)
        stream=io.BytesIO();wb.save(stream);wb.close();raw=stream.getvalue()
        code,d=self.call('/api/upload-plan',raw=raw,headers={'Content-Type':'application/octet-stream','X-File-Name':quote('回归测试_2026年10月.xlsx'),'X-Period':'2026-10','X-Actor':quote('回归测试')})
        self.assertEqual(code,200);self.assertTrue(d['ok']);sid=d['sub_id']
        for action in ['提交','批准']:
            code,result=self.call('/api/submission-action',{'id':sid,'action':action,'actor':'回归测试'})
            self.assertEqual(code,200,result);self.assertTrue(result['ok'],result)
        self.assertEqual(self.call('/api/submission?id='+str(sid))[1]['submission']['status'],'已锁定')
        before=len(self.call('/api/rows')[1]['plan']);self.assertGreater(before,0)
        self.assertTrue(self.call('/api/submission-action',{'id':sid,'action':'核销','actor':'回归测试'})[1]['ok'])
        server.sync_locked_plan()
        self.assertEqual(len(self.call('/api/rows')[1]['plan']),before)

if __name__=='__main__':unittest.main()
