import { test, expect } from '@playwright/test';

async function nav(page: any, name: string) {
  await page.locator('nav button').filter({ hasText: name }).click();
  await expect(page.locator('h1')).toHaveText(name);
}

test('独立应用十个模块、真实迁移用例和窄屏布局', async ({ page, request }) => {
  expect((await request.get('/api/cases')).ok()).toBeTruthy();
  const initial=await (await request.get('/api/cases')).json();
  expect(initial.length).toBe(456);
  await page.goto('/');
  await expect(page.getByRole('status',{name:'设备连接状态'})).toBeVisible();
  await expect(page.locator('nav button')).toHaveCount(10);
  for (const name of ['设备管理', '接口调试', '用例库', '用例编排', '测试执行', '响应时间', '老化测试', '报告中心', '公共方法']) {
    await nav(page,name);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBeTruthy();
  }
  await page.getByRole('combobox',{name:'当前机型'}).click();
  await page.getByText('Mercury E1',{exact:true}).click();
  await nav(page,'设备管理');
  await expect(page.getByRole('textbox',{name:'所选机型',exact:true})).toHaveValue('Mercury E1');
  await page.setViewportSize({width:390,height:844});
  await nav(page,'用例编排');
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBeTruthy();
});

test('设置和设备页面没有旧工程或模拟入口', async ({ page }) => {
  await page.goto('/'); await nav(page,'设备管理');
  await expect(page.getByRole('combobox',{name:'执行模式'})).toHaveCount(0);
  await expect(page.locator('.main-content').getByRole('button',{name:'连接设备',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(page.getByRole('tab',{name:'运行环境',exact:true})).toBeVisible();
  await expect(page.getByRole('tab',{name:'原有项目',exact:true})).toHaveCount(0);
  await expect(page.getByText('模拟设备',{exact:false})).toHaveCount(0);
});

test('独立创建和保存用例，不连接设备', async ({ page, request }) => {
  await page.goto('/'); await nav(page,'用例库');
  await expect(page.getByRole('tab',{name:'原 Python / Excel'})).toHaveCount(0);
  await page.getByRole('button',{name:'新增用例',exact:true}).click();
  await page.getByRole('textbox',{name:'用例名称'}).fill('独立草稿 · 等待现场填写');
  await page.getByRole('button',{name:'＋ 人工确认',exact:true}).click();
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page.getByText('草稿已保存在本机')).toBeVisible();
  const cases=await (await request.get('/api/cases')).json();
  expect(cases.length).toBe(457);
  expect(cases.some((c:any)=>c.name==='独立草稿 · 等待现场填写')).toBeTruthy();
  expect(await (await request.get('/api/tasks')).json()).toEqual([]);
});

test('缺少真实 SDK 时阻断连接检查，不生成测试结果', async ({ page, request }) => {
  await request.put('/api/settings/connection',{data:{mode:'hardware',model:'ultraarm_p1',address:'TEST-NOT-A-DEVICE',baudrate:115200}});
  await page.goto('/'); await nav(page,'设备管理');
  await page.locator('.main-content').getByRole('button',{name:'连接设备',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('确认');
  await page.getByRole('button',{name:'确认开始',exact:true}).click();
  await expect(page.getByText('请先导入并启用离线 SDK 环境',{exact:false}).first()).toBeVisible();
  expect(await (await request.get('/api/tasks')).json()).toEqual([]);
  expect(await (await request.get('/api/locks')).json()).toEqual([]);
});


test('450 使用 IP 和端口，右上角连接状态有校验依据', async ({ page, request }) => {
  await request.put('/api/settings/connection',{data:{mode:'hardware',model:'pro450',address:'',netport:4500,baudrate:115200}});
  await page.goto('/'); await nav(page,'设备管理');
  await expect(page.getByRole('spinbutton',{name:'TCP 端口',exact:true})).toHaveValue('4500');
  await expect(page.getByRole('spinbutton',{name:'波特率',exact:true})).toHaveCount(0);
  await expect(page.getByRole('status',{name:'设备连接状态'})).toHaveText('已断开');
  await expect(page.locator('header').getByRole('button',{name:'连接设备',exact:true})).toBeVisible();
});

test('右上角仅在持续遥测有效时显示已连接，过期立即失效', async ({ page, request }) => {
  const connection={mode:'hardware',model:'pro450',address:'UI-TEST-ONLY',netport:4500,baudrate:115200};
  await request.put('/api/settings/connection',{data:connection});
  let stale=false;
  await page.route('**/api/tasks',route => route.fulfill({json:[{id:'test-ui-session',kind:'connection',state:'connected',request:{connection},snapshot:{},summary:{},created_at:new Date().toISOString(),telemetry:{quality:'ok',moving:false,positions:[0,0,0,0,0,0],time:new Date(Date.now()-(stale?10000:0)).toISOString()}}]}));
  await page.goto('/');
  await expect(page.getByRole('status',{name:'设备连接状态'})).toHaveText('已连接');
  await expect(page.locator('header').getByRole('button',{name:'断开设备',exact:true})).toBeVisible();
  stale=true;
  await expect(page.getByRole('status',{name:'设备连接状态'})).toHaveText('连接保留 · 状态待更新',{timeout:6000});
});


test('调试本页连续调用、显示日志、异常后保持全局连接', async ({ page, request }) => {
  const connection={mode:'hardware',model:'pro450',address:'UI-DEBUG-ONLY',netport:4500,baudrate:115200};
  await request.put('/api/settings/connection',{data:connection});
  const session:any={id:'ui-session',kind:'connection',state:'connected',request:{connection},snapshot:{},summary:{device_info:{sdk_version:'ui-test',read_at:new Date().toISOString(),fields:{controller:{label:'主控版本',status:'ok',value:1.2},tool:{label:'末端版本',status:'unsupported'},screen:{label:'屏幕版本',status:'error',error:'读取超时'}}}},created_at:new Date().toISOString()};
  let calls:any[]=[];
  let disconnects=0;
  const freshSession=() => ({...session,telemetry:{quality:'ok',moving:false,positions:[0,0,0,0,0,0],time:new Date().toISOString()}});
  await page.route('**/api/apis?*',route => route.fulfill({json:{sdk_version:'ui-test',apis:[{name:'get_angles',signature:'get_angles()',parameters:[]}]}}));
  await page.route('**/api/tasks',async route => {
    if(route.request().method()==='GET')return route.fulfill({json:[...calls].reverse().concat([freshSession()])});
    const body=route.request().postDataJSON();
    expect(body.kind).toBe('debug');
    const id='ui-call-'+(calls.length+1);
    calls.push({id,kind:'debug',state:calls.length ? 'failed':'completed',request:body,snapshot:{},summary:calls.length ? {state:'failed',error:'ValueError: test-input'} : {state:'completed',result:[1,2,3]},created_at:new Date().toISOString(),telemetry:{}});
    return route.fulfill({json:{id},status:201});
  });
  await page.route('**/api/tasks/*/control',route => {disconnects++;return route.fulfill({json:{}});});
  await page.route('**/api/tasks/*',route => route.fulfill({json:calls.find(t=>t.id===route.request().url().split('/').pop()) || freshSession()}));
  await page.routeWebSocket('**/api/tasks/*/stream',ws => {
    const failed=ws.url().includes('ui-call-2');
    ws.send(JSON.stringify({seq:1,type:'return',level:failed?'ERROR':'INFO',api:'get_angles',message:failed?'调试异常已记录':'get_angles 返回',result:[1,2,3],sdk_ms:2.5,time:new Date().toISOString()}));
  });
  await page.goto('/');await nav(page,'接口调试');
  await page.getByRole('button',{name:'调用接口',exact:true}).click();
  await expect(page.locator('h1')).toHaveText('接口调试');
  await expect(page.getByText('get_angles 返回',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'调用接口',exact:true}).click();
  await expect(page.getByText('调试异常已记录',{exact:true})).toBeVisible();
  await expect(page.getByRole('status',{name:'设备连接状态'})).toHaveText('已连接');
  expect(calls).toHaveLength(2);expect(disconnects).toBe(0);
  session.state='degraded';
  await expect(page.getByRole('status',{name:'设备连接状态'})).toHaveText('状态读取重试中');
  await page.getByRole('button',{name:'调用接口',exact:true}).click();
  await expect.poll(()=>calls.length).toBe(3);
  await nav(page,'设备管理');
  await expect(page.getByText('主控版本',{exact:true})).toBeVisible();
  await expect(page.getByText('1.2',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'确认安全后解除隔离'})).toHaveCount(0);
});

test('迁移用例载入后可查看来源，待完善条目不能直接执行', async ({ page, request }) => {
  const loaded=await request.post('/api/case-repository/load');
  expect(loaded.ok()).toBeTruthy();
  const cases=await (await request.get('/api/cases')).json();
  expect(cases.some((c:any)=>c.migration?.workbook)).toBeTruthy();
  await request.put('/api/settings/connection',{data:{mode:'hardware',model:'ultraarm_p1',address:'',baudrate:115200}});
  await page.goto('/');await nav(page,'用例库');
  await expect(page.getByText('用例文件与 Git 同步',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'迁移来源',exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'迁移来源',exact:true}).first().click();
  await expect(page.getByRole('dialog')).toContainText('仅查看，不执行');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
});
