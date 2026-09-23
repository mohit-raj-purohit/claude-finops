"""Printable HTML report -> PDF via the browser's print dialog (no dependencies)."""
import html
from datetime import datetime, timezone


def _who(acct):
    """"Mohit Raj Purohit <mohit@example.com>" when we know both, else whichever
    one we have. A report that leaves the house should name its account."""
    name, email = acct.get("name") or "", acct.get("email") or acct.get("label") or ""
    if name and email:
        return f"{name} <{email}>"
    return name or email or "unknown"


def _f(v, kind="usd"):
    if v is None:
        return "&mdash;"
    if kind == "usd":
        return f"${v:,.2f}"
    if kind == "int":
        return f"{int(v):,}"
    if kind == "pct":
        return f"{v:,.1f}%"
    return html.escape(str(v))


def build_report(a, f):
    ov = a.overview(f); sc = a.scorecard(f); fc = a.forecast(f)
    md = a.models(f); pj = a.projects(f); wt = a.waste(f)
    rc = a.recommendations(f); ad = a.advisor(f); ef = a.efficiency(f)
    cat = a.categories(f); bp = ov["billing_period"]
    lb = a.leaderboards(f, 10); hy = a.hygiene(f)

    def table(headers, rows):
        h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
        b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"

    parts = [f"""<!doctype html><html><head><meta charset="utf-8">
<title>Claude FinOps Report</title><style>
@page {{ size: A4; margin: 14mm; }}
body {{ font: 12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#101418; }}
h1 {{ font-size: 24px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:22px 0 8px;
  border-bottom:2px solid #101418; padding-bottom:4px; page-break-after:avoid; }}
.sub {{ color:#5b6570; margin-bottom:18px; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0 14px; font-size:11px; }}
th {{ text-align:left; background:#f2f4f7; padding:5px 7px; border-bottom:1px solid #d5dae1; }}
td {{ padding:5px 7px; border-bottom:1px solid #eceff3; vertical-align:top; }}
td:nth-child(n+2) {{ font-variant-numeric: tabular-nums; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }}
.kpi {{ border:1px solid #d5dae1; border-radius:6px; padding:9px 11px; }}
.kpi .l {{ font-size:9px; text-transform:uppercase; letter-spacing:.06em; color:#5b6570; }}
.kpi .v {{ font-size:19px; font-weight:650; margin-top:2px; }}
.badge {{ display:inline-block; font-size:9px; padding:1px 6px; border-radius:99px;
  border:1px solid #c3cad3; color:#5b6570; text-transform:uppercase; letter-spacing:.05em; }}
.est {{ background:#fff6e5; border-color:#e0b055; color:#8a5a00; }}
.fc  {{ background:#eef3ff; border-color:#8fa8e8; color:#2b47a8; }}
.rec {{ background:#f0f8f1; border-color:#8ec79a; color:#1d6b30; }}
.note {{ font-size:10px; color:#5b6570; font-style:italic; margin:6px 0 14px; }}
.score {{ font-size:44px; font-weight:700; }}
ul {{ margin:6px 0 12px 18px; padding:0; }} li {{ margin-bottom:4px; }}
</style></head><body>
<h1>Claude AI FinOps Report</h1>
<div class="sub">Account {html.escape(_who(a.settings['account']))} &middot;
 Billing period {bp['start']} &rarr; {bp['end']} &middot;
 Data {ov['date_range']['first']} &ndash; {ov['date_range']['last']} &middot;
 Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
<p><span class="badge est">Estimated</span> All costs are derived from token counts in your local
Claude Code transcripts multiplied by the configurable price table in
<code>config/pricing.json</code> (updated {html.escape(str(a.pricing.updated))}). Claude Code
transcripts contain no billed amounts, so no figure here is an actual invoice value.</p>

<h2>1. Executive summary</h2>
<div class="kpis">
  <div class="kpi"><div class="l">Estimated spend</div><div class="v">{_f(ov['est_cost_usd'])}</div></div>
  <div class="kpi"><div class="l">Billable tokens</div><div class="v">{_f(ov['billable_tokens'],'int')}</div></div>
  <div class="kpi"><div class="l">Requests</div><div class="v">{_f(ov['requests'],'int')}</div></div>
  <div class="kpi"><div class="l">Sessions</div><div class="v">{_f(ov['sessions'],'int')}</div></div>
  <div class="kpi"><div class="l">Prompts</div><div class="v">{_f(ov['prompts'],'int')}</div></div>
  <div class="kpi"><div class="l">Active days</div><div class="v">{_f(ov['active_days'],'int')}</div></div>
  <div class="kpi"><div class="l">Output tokens</div><div class="v">{_f(ov['output_tokens'],'int')}</div></div>
  <div class="kpi"><div class="l">Avg / active day</div><div class="v">{_f(ov['avg_cost_per_active_day'])}</div></div>
</div>

<h2>2. Context hygiene</h2>
{table(["Threshold", "Requests", "Cost", "Sessions", "Cost after first cross"],
  [(f"{int(thr):,}", _f(v['requests'],'int'), _f(v['cost_usd']), _f(v['sessions'],'int'),
    _f(v['cost_after_first_cross_usd'])) for thr, v in hy['above'].items()])}
<div class="note">"Cost after first cross" is the spend on requests made once a session first passed the
threshold in that column &mdash; not a saving, an observation of where spend concentrates.</div>

<h2>3. FinOps scorecard</h2>
<p><span class="score">{sc['score']}</span> / 100 &nbsp; grade <b>{sc['grade']}</b></p>
{table(["Dimension","Score","Detail"], [(d['name'], d['score'], html.escape(d['detail'])) for d in sc['dimensions']])}
<b>What is good</b><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in sc['what_is_good']) or '<li>&mdash;</li>'}</ul>
<b>Needs attention</b><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in sc['needs_attention']) or '<li>&mdash;</li>'}</ul>

<h2>4. What should I do? <span class="badge">Advisor</span></h2>
<ol>{''.join(f"<li><b>{html.escape(x['text'])}</b><br><span style='color:#5b6570'>{html.escape(x['detail'])}</span></li>" for x in ad['actions']) or '<li>No actions.</li>'}</ol>

<h2>5. Model breakdown <span class="badge est">Estimated cost</span></h2>
{table(["Model","Requests","Input","Output","Cache read","Cache write","Billable tokens","Est. cost","% cost"],
  [(html.escape(r['display_name']), _f(r['requests'],'int'), _f(r['input_tokens'],'int'),
    _f(r['output_tokens'],'int'), _f(r['cache_read_tokens'],'int'), _f(r['cache_write_tokens'],'int'),
    _f(r['tokens'],'int'), _f(r['cost']), _f(r['cost_pct'],'pct')) for r in md['rows']])}

<h2>6. Project breakdown</h2>
{table(["Project","Sessions","Prompts","Requests","Tokens","Est. cost","Avg / session"],
  [(html.escape(r['name']), _f(r['sessions'],'int'), _f(r['prompts'],'int'), _f(r['requests'],'int'),
    _f(r['tokens'],'int'), _f(r['cost']), _f(r['avg_cost_per_session'])) for r in pj[:20]])}

<h2>7. Spend by activity</h2>
{table(["Category","Prompts","Tokens","Est. cost","% of spend"],
  [(html.escape(r['category']), _f(r['prompts'],'int'), _f(r['tokens'],'int'), _f(r['cost']),
    _f(r['cost_pct'],'pct')) for r in cat['rows']])}
<div class="note">{html.escape(cat['note'])}</div>

<h2>8. Efficiency &amp; caching</h2>
{table(["Metric","Value"], [
  ("Output share of billable tokens", f"{ef['output_ratio']*100:.2f}%"),
  ("Output per prompt-side token", f"{ef['output_per_input']*100:.2f}%"),
  ("Cache hit ratio (by token)", (f"{ef['cache_hit_ratio']*100:.1f}%" if ef['cache_hit_ratio'] is not None else "&mdash;")),
  ("Cache reads as share of cache cost", (f"{ef['cache_read_cost_share']*100:.1f}%"
                                          if ef.get('cache_read_cost_share') is not None else "&mdash;")),
  ("Tokens per request", _f(ef['tokens_per_request'],'int')),
  ("Avg context per request", _f(ef['avg_context_tokens'],'int')),
  ("Est. cost per 1K output tokens", f"${ef['cost_per_1k_output']:.3f}"),
  ("Est. cost with caching", _f(ef['cache']['cost_with_cache'])),
  ("Est. cost without caching", _f(ef['cache']['cost_without_cache'])),
  ("Uncached counterfactual — not a saving", f"{_f(ef['cache']['uncached_counterfactual_delta_usd'])} ({ef['cache']['uncached_counterfactual_pct']}%)"),
])}

<h2>9. Top 10 most expensive prompts <span class="badge est">Estimated</span></h2>
{table(["#","Prompt","Model","Tokens","Est. cost","Date"],
  [(i, html.escape((r['preview'] or '')[:120]), html.escape((r['models'] or '')[:40]),
    _f(r['ptokens'],'int'), _f(r['pcost']), r['day'])
   for i, r in enumerate(lb['most_expensive'], 1)])}

<h2>10. Waste detection <span class="badge est">Estimated exposure</span></h2>
<p><b>Estimated excess: {_f(wt['estimated_excess_usd'])} ({wt['excess_pct']}%)</b> of
{_f(wt['total_cost_usd'])} — how much more the flagged work cost than a reasonable baseline.
It sits inside {_f(wt['exposed_cost_usd'])} ({wt['exposed_pct']}%) of exposed spend across
{wt['affected_prompts']} prompts and {wt['affected_sessions']} sessions.</p>
{table(["Severity","Finding","Est. excess","Exposed","Excess measured as","Recommended action"],
  [(x['severity'].upper(), html.escape(x['title']), _f(x['est_excess_usd']), _f(x['est_cost_usd']),
    html.escape(x['excess_basis']), html.escape(x['recommended_action'] or ''))
   for x in wt['findings']])}
<div class="note">{html.escape(wt['note'])}</div>

<h2>11. Optimization recommendations <span class="badge rec">Recommendation</span></h2>
{table(["Recommendation","Spend involved","Basis"],
  [(html.escape(r['title']), _f(r.get('actual_cost_usd')), html.escape(r.get('basis','')))
   for r in rc['recommendations']]) if rc['recommendations'] else '<p>No recommendation met the evidence threshold.</p>'}
<div class="note">Observations only. No saving is estimated: what an alternative would have cost is a counterfactual.</div>

<h2>12. Forecast <span class="badge fc">Forecast</span></h2>"""]

    if fc.get("available"):
        parts.append(f"""<p>Method: {html.escape(fc['method'])} over {fc['sample_days']} days.
Period to date {_f(fc['period_used'])} with {fc['remaining_days']} days remaining.</p>
{table(["Scenario","Daily rate","Projected end of period"],
  [(k.title(), _f(v['daily_rate']), _f(v['end_of_period_cost'])) for k, v in fc['scenarios'].items()])}
{table(["Horizon","Projection"], [
  ("End of day", _f(fc['end_of_day_cost'])),
  ("End of week", _f(fc['end_of_week_cost'])),
  ("End of billing period (expected)", _f(fc['scenarios']['expected']['end_of_period_cost'])),
  ("Limit exhaustion date", html.escape(str(fc['limit_exhaustion_date']))),
])}""")
    else:
        parts.append("<p>Not enough data in range to forecast.</p>")

    parts.append(f"""
<h2>13. Data provenance</h2>
{table(["Field","Value"], [
  ("Source", html.escape(str(a.meta.get('source_dir')))),
  ("Transcript files parsed", html.escape(str(a.meta.get('transcript_files')))),
  ("Warehouse built", html.escape(str(a.meta.get('built_at')))),
  ("Cost basis", "Estimated from token counts &times; config/pricing.json"),
  ("Price table updated", html.escape(str(a.pricing.updated))),
  ("Plan limits", "Unavailable from connected Claude data unless configured in config/settings.json"),
])}
<div class="note">Claude Code transcripts do not expose plan allowances, remaining credits, billed
amounts, or git commit/PR linkage. Those fields are reported as unavailable rather than estimated.</div>
</body></html>""")
    return "".join(parts)
