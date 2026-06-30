// ============================================================
// AI股票分析云服务 - 部署到 Render/Railway
// 每日9:00盘前分析 + 9:30开盘异动 -> 推送到微信
// 无需电脑开机，24小时在线
// ============================================================
const http = require('http');
const https = require('https');

// ===================== 配置 =====================
const CONFIG = {
  deepseekKey: process.env.DEEPSEEK_API_KEY || '',
  pushplusToken: process.env.PUSHPLUS_TOKEN || '',
  deepseekUrl: 'https://api.deepseek.com/chat/completions',
  pushplusUrl: 'https://www.pushplus.plus/send',
  timeZone: 8, // 北京时间
};

// ===================== 工具函数 =====================
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const opts = new URL(url);
    opts.headers = { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn' };
    mod.get(opts, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const raw = Buffer.concat(chunks);
        try { resolve(new TextDecoder('gbk').decode(raw)); } catch(e) { resolve(raw.toString()); }
      });
    }).on('error', reject);
  });
}

// ===================== 数据获取 =====================
async function getIndexes() {
  const raw = await httpGet('https://hq.sinajs.cn/list=sh000001,sz399001,sz399006,sh000688');
  const result = [];
  raw.split('\n').forEach(line => {
    const m = line.match(/"([^"]+)"/);
    if (!m) return;
    const d = m[1].split(',');
    if (d.length > 5 && d[0]) {
      const cur = parseFloat(d[3]), yc = parseFloat(d[2]);
      result.push({ name: d[0], price: d[3], chg: yc > 0 ? ((cur - yc) / yc * 100).toFixed(2) : '0' });
    }
  });
  return result;
}

async function getHotStocks() {
  const raw = await httpGet('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=10&sort=changepercent&asc=0&node=hs_a&_s_r_a=init');
  try {
    // 过滤：排除688、92开头、ST股票
    const list = JSON.parse(raw) || [];
    return list.filter(s => {
      const code = String(s.code || '');
      if (code.startsWith('688')) return false;  // 科创板
      if (code.startsWith('92')) return false;    // 北交所
      if (code.startsWith('4')) return false;     // 北交所
      if ((s.name || '').includes('ST') || (s.name || '').includes('*')) return false; // ST股
      return true;
    }).slice(0, 8).map(s => ({
      code: s.code, name: s.name, price: String(s.trade || '--'),
      chg: String(s.changepercent || '0'), high: String(s.high || '--'), low: String(s.low || '--')
    }));
  } catch(e) { return []; }
}

// ===================== AI分析 =====================
function callDeepseek(messages) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify({
      model: 'deepseek-chat',
      messages: messages,
      temperature: 0.7,
      max_tokens: 1536
    });
    const opts = {
      hostname: 'api.deepseek.com', path: '/chat/completions', method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + CONFIG.deepseekKey,
        'Content-Length': Buffer.byteLength(data)
      }
    };
    const req = https.request(opts, res => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(body);
          resolve(json.choices?.[0]?.message?.content || '分析失败');
        } catch(e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

// ===================== 推送微信 =====================
function pushToWechat(title, content) {
  return new Promise((resolve) => {
    const data = JSON.stringify({
      token: CONFIG.pushplusToken, title: title,
      content: content, template: 'txt'
    });
    const opts = {
      hostname: 'www.pushplus.plus', path: '/send', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) }
    };
    const req = https.request(opts, res => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(body).code === 200); } catch(e) { resolve(false); }
      });
    });
    req.on('error', () => resolve(false));
    req.write(data);
    req.end();
  });
}

// ===================== 分析逻辑 =====================
async function runMorningAnalysis() {
  console.log('=== 盘前分析开始 ===');
  const [indexes, hotStocks] = await Promise.all([getIndexes(), getHotStocks()]);

  const sysPrompt = '你是职业A股分析师，擅长炒股养家情绪周期理论。\n核心规则：\n1. 数据必须真实，严禁编造\n2. 每只推荐附上明确逻辑和风险提示\n3. 严禁推荐688开头、92开头、ST股票\n分析维度：技术面、消息面、资金面、基本面';

  const userMsg = '现在是北京时间9:00，A股今日盘前分析。\n\n【指数】\n' +
    indexes.map(i => i.name + ' ' + i.price + ' (' + i.chg + '%)').join('\n') +
    '\n\n【热门个股】\n' +
    hotStocks.slice(0, 5).map(s => s.code + ' ' + s.name + ' 涨幅' + s.chg + '%').join('\n') +
    '\n\n请选出3只今日值得关注的股票，严格按格式：\n📋 今日盘前关注（3只）\n1️⃣ [名称/代码]\n📌 关注逻辑：\n⚠️ 风险提示：\n\n📊 大盘研判：\n🎯 操作策略：';

  const analysis = await callDeepseek([
    { role: 'system', content: sysPrompt },
    { role: 'user', content: userMsg }
  ]);

  const ok = await pushToWechat('📋 ' + new Date().toLocaleDateString('zh-CN') + ' 盘前关注', analysis);
  console.log('推送结果:', ok ? '成功' : '失败');
}

async function runOpeningAnalysis() {
  console.log('=== 开盘异动分析开始 ===');
  const [indexes, hotStocks] = await Promise.all([getIndexes(), getHotStocks()]);

  const sysPrompt = '你是职业A股短线交易分析师，擅长捕捉开盘异动。\n规则：\n1. 基于真实数据\n2. 关注开盘异动（高开、放量）\n3. 严禁推荐688开头、92开头、ST股票';

  const userMsg = '现在是9:30，开盘异动分析。\n\n【指数】\n' +
    indexes.map(i => i.name + ' ' + i.price + ' (' + i.chg + '%)').join('\n') +
    '\n\n【热门个股】\n' +
    hotStocks.slice(0, 5).map(s => s.code + ' ' + s.name + ' 涨幅' + s.chg + '%').join('\n') +
    '\n\n选出3只开盘异动股，格式：\n🔥 开盘异动追踪（3只）\n1️⃣ [名称/代码]\n📊 开盘表现：\n📌 异动原因：\n⚠️ 注意：';

  const analysis = await callDeepseek([
    { role: 'system', content: sysPrompt },
    { role: 'user', content: userMsg }
  ]);

  const ok = await pushToWechat('🔥 ' + new Date().toLocaleDateString('zh-CN') + ' 开盘异动', analysis);
  console.log('推送结果:', ok ? '成功' : '失败');
}

// ===================== 定时调度 =====================
function getBeijingTime() {
  const now = new Date();
  const bj = new Date(now.getTime() + CONFIG.timeZone * 3600000);
  return {
    h: bj.getUTCHours(),
    m: bj.getUTCMinutes(),
    d: bj.getUTCDay(),
    str: bj.toISOString().replace('T', ' ').slice(0, 19)
  };
}

let lastMorningRun = '', lastOpeningRun = '';

function checkSchedule() {
  const t = getBeijingTime();
  const today = new Date().toISOString().slice(0, 10);

  // 周末不跑
  if (t.d === 0 || t.d === 6) return;

  // 9:00 盘前分析
  if (t.h === 9 && t.m === 0 && lastMorningRun !== today) {
    lastMorningRun = today;
    runMorningAnalysis().catch(e => console.error('盘前分析失败:', e.message));
  }

  // 9:30 开盘异动
  if (t.h === 9 && t.m === 30 && lastOpeningRun !== today) {
    lastOpeningRun = today;
    runOpeningAnalysis().catch(e => console.error('开盘分析失败:', e.message));
  }
}

// ===================== HTTP服务（用于健康检查） =====================
const server = http.createServer((req, res) => {
  const t = getBeijingTime();
  if (req.url === '/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'running', time: t.str,
      morning: lastMorningRun, opening: lastOpeningRun,
      nextCheck: '9:00 / 9:30 北京时间'
    }));
    return;
  }
  if (req.url === '/trigger-morning') {
    runMorningAnalysis().then(() => {
      res.writeHead(200); res.end('morning done');
    }).catch(e => { res.writeHead(500); res.end(e.message); });
    return;
  }
  if (req.url === '/trigger-opening') {
    runOpeningAnalysis().then(() => {
      res.writeHead(200); res.end('opening done');
    }).catch(e => { res.writeHead(500); res.end(e.message); });
    return;
  }
  res.writeHead(200); res.end('AI Stock Analysis Server Running');
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log('==============================================');
  console.log('  AI 股票分析云服务已启动');
  console.log('  服务器时间:', getBeijingTime().str);
  console.log('  每日 9:00 / 9:30 自动分析推送');
  console.log('  HTTP端口:', PORT);
  console.log('==============================================');

  // 每30秒检查一次是否需要运行
  setInterval(checkSchedule, 30000);
  // 启动时立即检查一次
  setTimeout(checkSchedule, 5000);
});
