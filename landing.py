"""
Landing page for the Tickerbase website.

It reuses the EXACT same CSS as the report (imported from tickerbase) so the
inline report looks identical to the standalone version, then adds the search
box and the small bit of JavaScript that calls /analyze and injects the result.
"""

import tickerbase as sl

# Extra styles just for the landing/search experience (report styles come from sl.CSS)
EXTRA_CSS = """
.search{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;max-width:560px;margin:24px auto 10px;}
.search input{flex:1;min-width:200px;font-size:18px;padding:14px 18px;border:1.5px solid var(--border2);
  border-radius:var(--r);background:var(--surface);color:var(--ink);text-transform:uppercase;
  letter-spacing:.06em;font-weight:600;text-align:center;transition:.15s;}
.search input::placeholder{font-weight:400;letter-spacing:.02em;color:var(--hint);text-transform:none;}
.search input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-bg);}
.search input:hover{border-color:var(--border2);}
.search button{font-size:16px;font-weight:600;padding:14px 32px;border-radius:var(--r);border:none;
  background:var(--accent);color:#fff;cursor:pointer;transition:filter .12s ease,transform .08s ease;
  box-shadow:0 1px 2px rgba(0,0,0,.08);}
.search button:hover{filter:brightness(1.08);}.search button:active{transform:scale(.97);}
.search button:disabled{opacity:.55;cursor:default;}
.examples{text-align:center;font-size:13px;color:var(--hint);margin:0 0 10px;}
.examples span{color:var(--accent);cursor:pointer;font-weight:600;margin:0 5px;padding:2px 4px;border-radius:5px;transition:background .12s ease;}
.examples span:hover{background:var(--accent-bg);text-decoration:none;}
.status{max-width:680px;margin:6px auto 0;font-size:14px;color:var(--muted);text-align:center;min-height:22px;padding:0 8px;}
.status.err{color:var(--red);}
.spin{display:inline-block;width:15px;height:15px;border:2px solid var(--border2);border-top-color:var(--accent);
  border-radius:50%;animation:sp .7s linear infinite;vertical-align:-3px;margin-right:8px;}
@keyframes sp{to{transform:rotate(360deg)}}
.report-meta{text-align:center;font-size:12px;color:var(--hint);margin:14px 0 20px;font-variant-numeric:tabular-nums;}
.starters{max-width:760px;margin:18px auto 0;}
.starters>summary{cursor:pointer;list-style:none;text-align:center;font-size:13.5px;
  color:var(--accent);font-weight:600;padding:8px;border-radius:var(--r-sm);
  transition:background .12s ease;}
.starters>summary::-webkit-details-marker{display:none;}
.starters>summary:hover{background:var(--accent-bg);}
.sgroup{margin:14px 0 0;}
.sgroup h4{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  margin:0 0 7px;font-weight:600;}
.sgroup p{font-size:12px;color:var(--hint);margin:0 0 8px;line-height:1.5;}
.chips{display:flex;flex-wrap:wrap;gap:7px;}
.tchip{font-size:13px;font-weight:600;padding:6px 12px;border-radius:20px;cursor:pointer;
  background:var(--surface2);color:var(--ink);border:1px solid var(--border);
  transition:background .12s ease,border-color .12s ease;font-variant-numeric:tabular-nums;}
.tchip:hover{background:var(--accent-bg);border-color:var(--accent);color:var(--accent);}
.sdisc{font-size:11.5px;color:var(--hint);text-align:center;margin:16px 0 0;line-height:1.6;}
.intro{max-width:620px;margin:18px auto 0;text-align:center;color:var(--muted);font-size:14px;line-height:1.6;}
#report{margin-top:24px;}
.footer{text-align:center;font-size:12px;color:var(--hint);margin-top:52px;padding-top:20px;border-top:1px solid var(--border);line-height:1.7;}
"""

BADGE_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-6"/></svg>')

# Note: braces in the JS are doubled {{ }} because this is an f-string.
LANDING_PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tickerbase, one-stop stock research</title>
<style>{sl.CSS}{EXTRA_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="hero" id="hero">
    <div class="badge">{BADGE_SVG}</div>
    <h1>Tickerbase</h1>
    <p class="tk">Type a ticker. See everything a serious investor weighs before buying, in plain English.</p>
  </div>

  <div class="search">
    <input type="text" id="ticker" placeholder="Enter a ticker, e.g. AAPL" autocomplete="off" spellcheck="false">
    <button id="goBtn">Analyze</button>
  </div>
  <p class="examples">Try:
    <span data-t="AAPL">AAPL</span><span data-t="MSFT">MSFT</span><span data-t="NVDA">NVDA</span>
    <span data-t="KO">KO</span><span data-t="JPM">JPM</span><span data-t="TSLA">TSLA</span>
  </p>
  <div class="status" id="status"></div>

  <details class="starters" id="starters">
    <summary>Not sure where to start? Browse companies by category</summary>
    <div class="sgroup"><h4>Megacap tech</h4><p>The largest, most widely held technology companies. A reasonable place to see what a mature, profitable business looks like in the numbers.</p><div class="chips"><span class="tchip" data-t="AAPL">AAPL</span><span class="tchip" data-t="MSFT">MSFT</span><span class="tchip" data-t="GOOGL">GOOGL</span><span class="tchip" data-t="AMZN">AMZN</span><span class="tchip" data-t="META">META</span><span class="tchip" data-t="NVDA">NVDA</span></div></div>
    <div class="sgroup"><h4>AI &amp; semiconductors</h4><p>Chipmakers and the hardware behind AI. Cyclical, capital-heavy, and currently priced for a lot of growth.</p><div class="chips"><span class="tchip" data-t="NVDA">NVDA</span><span class="tchip" data-t="AMD">AMD</span><span class="tchip" data-t="AVGO">AVGO</span><span class="tchip" data-t="TSM">TSM</span><span class="tchip" data-t="ASML">ASML</span><span class="tchip" data-t="MU">MU</span><span class="tchip" data-t="ARM">ARM</span></div></div>
    <div class="sgroup"><h4>Financials</h4><p>Banks, card networks and insurers. Read these with different yardsticks: P/B and ROE matter more than margins here.</p><div class="chips"><span class="tchip" data-t="JPM">JPM</span><span class="tchip" data-t="BAC">BAC</span><span class="tchip" data-t="GS">GS</span><span class="tchip" data-t="V">V</span><span class="tchip" data-t="MA">MA</span><span class="tchip" data-t="BRK-B">BRK-B</span></div></div>
    <div class="sgroup"><h4>Healthcare &amp; pharma</h4><p>Drugmakers and insurers. Watch the pipeline and patent cliffs, which no ratio on this page can show you.</p><div class="chips"><span class="tchip" data-t="LLY">LLY</span><span class="tchip" data-t="JNJ">JNJ</span><span class="tchip" data-t="UNH">UNH</span><span class="tchip" data-t="ABBV">ABBV</span><span class="tchip" data-t="MRK">MRK</span><span class="tchip" data-t="PFE">PFE</span></div></div>
    <div class="sgroup"><h4>Consumer staples &amp; retail</h4><p>Slower, steadier businesses. Useful contrast to tech: thin margins, low growth, durable demand.</p><div class="chips"><span class="tchip" data-t="COST">COST</span><span class="tchip" data-t="WMT">WMT</span><span class="tchip" data-t="KO">KO</span><span class="tchip" data-t="PG">PG</span><span class="tchip" data-t="NKE">NKE</span><span class="tchip" data-t="SBUX">SBUX</span></div></div>
    <div class="sgroup"><h4>Higher growth, higher risk</h4><p>Smaller and newer companies growing fast, often not yet consistently profitable. Far more volatile, and the metrics here are noisier.</p><div class="chips"><span class="tchip" data-t="RBRK">RBRK</span><span class="tchip" data-t="SNOW">SNOW</span><span class="tchip" data-t="DDOG">DDOG</span><span class="tchip" data-t="CRWD">CRWD</span><span class="tchip" data-t="NET">NET</span><span class="tchip" data-t="PLTR">PLTR</span><span class="tchip" data-t="HOOD">HOOD</span><span class="tchip" data-t="SOFI">SOFI</span></div></div>
    <p class="sdisc">These are common starting points for research, grouped by category, not picks or predictions.
    Tickerbase does not rank or recommend stocks. Listings may go stale as companies merge, delist or change names.</p>
  </details>

  <p class="intro" id="intro">Tickerbase pulls a company's business, valuation, financial health, growth, risk,
  and what analysts &amp; insiders are doing, then sums it up with a plain-English verdict. It's an educational
  research tool, not investment advice. Enter a ticker above to begin.</p>

  <div id="report"></div>

  <div class="footer">Educational use only · not investment advice · data from Finnhub and Twelve Data</div>
</div>

<script>
const $ = id => document.getElementById(id);
let busy = false;

async function analyze(t){{
  t = (t || $("ticker").value).trim().toUpperCase();
  if(!t){{ setStatus("Please enter a ticker symbol.", true); return; }}
  if(busy) return;
  busy = true;
  $("goBtn").disabled = true;
  $("intro").style.display = "none";
  $("starters").style.display = "none";
  $("report").innerHTML = "";
  setStatus('<span class="spin"></span>Loading ' + t + ' … (this can take a few seconds)');
  try {{
    const res = await fetch("/analyze?ticker=" + encodeURIComponent(t));
    const data = await res.json();
    if(data.ok){{
      $("report").innerHTML = data.html;
      setStatus(data.partial ? "Loaded, a few optional fields were unavailable, the rest is shown below."
                             : (data.cached ? "" : ""));
      // scroll the report into view on small screens
      $("report").scrollIntoView({{behavior:"smooth", block:"start"}});
    }} else {{
      setStatus(data.error || "Something went wrong. Please try again.", true);
      $("intro").style.display = "block";
    $("starters").style.display = "";
      $("starters").style.display = "";
    }}
  }} catch(e) {{
    setStatus("Couldn't reach the server. Check your connection and try again.", true);
    $("intro").style.display = "block";
    $("starters").style.display = "";
  }} finally {{
    busy = false;
    $("goBtn").disabled = false;
  }}
}}

function setStatus(html, isErr){{
  const s = $("status");
  s.innerHTML = html || "";
  s.className = "status" + (isErr ? " err" : "");
}}

$("goBtn").onclick = () => analyze();
$("ticker").addEventListener("keydown", e => {{ if(e.key === "Enter") analyze(); }});
document.querySelectorAll(".examples span, .tchip").forEach(b =>
  b.onclick = () => {{ $("ticker").value = b.dataset.t; analyze(b.dataset.t); }});

// allow deep links like /?t=AAPL
const qs = new URLSearchParams(location.search).get("t");
if(qs){{ $("ticker").value = qs.toUpperCase(); analyze(qs); }}
</script>
</body>
</html>"""
