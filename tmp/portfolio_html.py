PORTFOLIO_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yuki — AI Assistant</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect rx='20' width='100' height='100' fill='%23818CF8'/><text x='50' y='68' font-size='55' text-anchor='middle' fill='white' font-family='system-ui' font-weight='bold'>Y</text></svg>">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#0F172A;color:#e2e8f0;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;top:0;left:0;width:100%;height:100%;background:radial-gradient(ellipse at 30% 20%,rgba(129,140,248,.1) 0%,transparent 50%),radial-gradient(ellipse at 70% 80%,rgba(244,114,182,.06) 0%,transparent 50%),radial-gradient(ellipse at 50% 50%,rgba(34,211,238,.04) 0%,transparent 50%);z-index:-1}
.glass{background:rgba(30,41,59,.5);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.08);border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.3)}

/* Hero */
.hero{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:40px 24px;position:relative}
.hero-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 20px;border-radius:30px;font-size:.85em;font-weight:500;margin-bottom:24px;color:#818CF8;background:rgba(129,140,248,.1);border:1px solid rgba(129,140,248,.2)}
.hero-badge .dot{width:8px;height:8px;border-radius:50%;background:#22C55E;box-shadow:0 0 8px #22C55E;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.hero h1{font-size:clamp(2.5em,6vw,4.5em);font-weight:800;line-height:1.1;margin-bottom:16px;letter-spacing:-1px}
.hero h1 span{background:linear-gradient(135deg,#818CF8,#F472B6,#22D3EE);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.hero p{font-size:1.2em;color:#94a3b8;max-width:600px;line-height:1.7;margin-bottom:32px}
.hero-ascii{font-family:'Courier New',monospace;font-size:.8em;color:#475569;line-height:1.4;margin-bottom:40px;padding:20px;border:1px solid rgba(255,255,255,.05);border-radius:12px;background:rgba(15,23,42,.5)}

/* Sections */
.section{max-width:1000px;margin:0 auto;padding:80px 24px}
.section-title{font-size:1.8em;font-weight:700;text-align:center;margin-bottom:12px}
.section-title span{color:#818CF8}
.section-sub{text-align:center;color:#64748b;margin-bottom:48px;font-size:.95em}

/* Features Grid */
.features{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.feature{padding:28px;transition:transform .2s,box-shadow .2s}
.feature:hover{transform:translateY(-4px);box-shadow:0 16px 48px rgba(0,0,0,.4)}
.feature-icon{font-size:2em;margin-bottom:12px}
.feature h3{color:#fff;font-size:1.05em;margin-bottom:8px}
.feature p{color:#94a3b8;font-size:.85em;line-height:1.6}

/* Tech Stack */
.tech-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.tech-item{padding:24px;text-align:center;transition:transform .2s}
.tech-item:hover{transform:scale(1.05)}
.tech-item .name{color:#fff;font-weight:600;font-size:.95em;margin-top:8px}
.tech-item .desc{color:#64748b;font-size:.75em;margin-top:4px}
.tech-dot{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;margin:0 auto;font-size:1.4em;font-weight:700}

/* Stats */
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:48px}
.stat-card{text-align:center;padding:32px}
.stat-card .val{font-size:2.4em;font-weight:800;color:#818CF8}
.stat-card .label{color:#64748b;font-size:.85em;margin-top:4px}

/* Footer */
.footer{text-align:center;padding:40px 24px;color:#475569;font-size:.8em;border-top:1px solid rgba(255,255,255,.05)}
.footer a{color:#818CF8;text-decoration:none}

@media(max-width:768px){.features{grid-template-columns:1fr}.tech-grid{grid-template-columns:repeat(2,1fr)}.stats-row{grid-template-columns:1fr}}
</style>
</head>
<body>

<!-- Hero -->
<section class="hero">
  <div class="hero-badge"><div class="dot"></div> Live &amp; Running</div>
  <h1>Meet <span>Yuki</span></h1>
  <p>Personal AI assistant yang dibangun dengan hati. Web search, vision, cuaca, memory, dan 10+ skill — semuanya gratis dan open-source.</p>
  <div class="hero-ascii">
    <pre style="text-align:center">
    \\   /
     .-.
  &#x2500;&#x2500;(   )&#x2500;&#x2500;
  &#x2500;&#x2500; &#x2018;&#x2019;&#x2500;&#x2500;
     &#x2018;&#x2019; &#x2018;&#x2019;
    </pre>
  </div>
</section>

<!-- Features -->
<div class="section">
  <div class="section-title">Fitur <span>Unggulan</span></div>
  <div class="section-sub">Semua yang dibutuhkan dalam satu asisten AI</div>
  <div class="features">
    <div class="feature glass">
      <div class="feature-icon">&#x1F50D;</div>
      <h3>Web Search</h3>
      <p>Cari informasi real-time via TinyFish & Tavily. Selalu update dengan berita terkini.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F441;</div>
      <h3>Vision</h3>
      <p>Analisis gambar dan video. Kirim foto, Yuki akan menjelaskan apa yang dilihat.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F326;</div>
      <h3>Weather Info</h3>
      <p>Cek cuaca real-time untuk kota manapun. Data dari Open-Meteo, gratis tanpa API key.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F4BE;</div>
      <h3>Long-term Memory</h3>
      <p>Yuki ingat percakapan sebelumnya. Semua disimpan di Google Sheets, always learning.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F4D6;</div>
      <h3>10+ Skills</h3>
      <p>Translate, summarize, write, research, extract, crawl, calculator, dan masih banyak lagi.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F6E1;</div>
      <h3>Injection Protection</h3>
      <p>2-layer security: system prompt hardening + input filtering. Yuki tidak bisa di-jailbreak.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F3AF;</div>
      <h3>Adaptive Behavior</h3>
      <p>Yuki belajar dari interaksi. Auto-react emoji, mood detection, dan personality yang berkembang.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x1F4CA;</div>
      <h3>Monitoring Dashboard</h3>
      <p>Real-time dashboard dengan Chart.js. Response time, error tracking, usage analytics.</p>
    </div>
    <div class="feature glass">
      <div class="feature-icon">&#x2705;</div>
      <h3>Health Check + Backup</h3>
      <p>Auto-monitoring setiap 5 menit. Auto-backup Google Sheets setiap hari.</p>
    </div>
  </div>
</div>

<!-- Tech Stack -->
<div class="section">
  <div class="section-title">Tech <span>Stack</span></div>
  <div class="section-sub">Dibangun dengan teknologi modern dan gratis</div>
  <div class="tech-grid">
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(129,140,248,.15);color:#818CF8">G</div>
      <div class="name">Gemini 3.1 Flash Lite</div>
      <div class="desc">Default AI model</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(34,197,94,.15);color:#22C55E">TF</div>
      <div class="name">TinyFish</div>
      <div class="desc">Free web search</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(244,114,182,.15);color:#F472B6">Tv</div>
      <div class="name">Tavily</div>
      <div class="desc">Deep search & extract</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(34,211,238,.15);color:#22D3EE">OM</div>
      <div class="name">Open-Meteo</div>
      <div class="desc">Weather API (free)</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(245,158,11,.15);color:#F59E0B">GS</div>
      <div class="name">Google Sheets</div>
      <div class="desc">Memory backend</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(168,85,247,.15);color:#A855F7">OR</div>
      <div class="name">OpenRouter</div>
      <div class="desc">Vision & fallback</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(239,68,68,.15);color:#EF4444">Py</div>
      <div class="name">Python + FastAPI</div>
      <div class="desc">Backend framework</div>
    </div>
    <div class="tech-item glass">
      <div class="tech-dot" style="background:rgba(99,102,241,.15);color:#6366F1">N</div>
      <div class="name">Nginx</div>
      <div class="desc">Reverse proxy</div>
    </div>
  </div>

  <div class="stats-row">
    <div class="stat-card glass">
      <div class="val" id="totalReqs">-</div>
      <div class="label">Total Requests</div>
    </div>
    <div class="stat-card glass">
      <div class="val" id="uptime">-</div>
      <div class="label">Uptime</div>
    </div>
    <div class="stat-card glass">
      <div class="val" id="status" style="color:#22C55E">Online</div>
      <div class="label">Status</div>
    </div>
  </div>
</div>

<!-- Footer -->
<div class="footer">
  <p>Built with &#x2661; by <a href="https://github.com/yuki71-s">Y71</a> &middot; Powered by <a href="https://github.com/yuki71-s/yuki-bot">Yuki Bot</a> &middot; 2026</p>
</div>

<script>
async function loadStats(){
  try{
    const r=await fetch('/health');const d=await r.json();
    document.getElementById('status').textContent='Online';
    document.getElementById('status').style.color='#22C55E';
  }catch(e){
    document.getElementById('status').textContent='Offline';
    document.getElementById('status').style.color='#EF4444';
  }
}
loadStats();
</script>
</body></html>"""
