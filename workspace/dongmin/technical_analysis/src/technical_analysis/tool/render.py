"""자료 묶음 → HTML 한 장, 크롬(화면 없이)으로 PNG, 텔레그램에 붙일 짧은 글."""

import json
import math
import re
import subprocess
import time
from html import escape
from pathlib import Path

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PNG_SIZE = (1680, 1050)


def out_dir() -> Path:
    return Path("~/quant-data/technical-analysis/out").expanduser()


def file_stem(meta: dict[str, object]) -> str:
    code = re.sub(r"[^0-9A-Za-z가-힣]+", "_", str(meta["code"])).strip("_")
    return f"{code}_{meta['date']}"


def write_html(payload: dict[str, object], path: Path) -> Path:
    template = (Path(__file__).parent / "page.html").read_text()
    meta = payload["meta"]
    assert isinstance(meta, dict)
    title = f"{meta['name']} 기술적 상태 {meta['date']}"
    body = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("__TITLE__", title).replace("__PAYLOAD__", body))
    return path


def write_png(html: Path, png: Path) -> Path:
    """크롬을 화면 없이 띄워 페이지를 캡처한다(차트 라이브러리를 받느라 인터넷이 필요하다)."""
    if not CHROME.exists():
        raise RuntimeError(f"PNG 를 만들 크롬이 없다: path={CHROME}")
    w, h = PNG_SIZE
    cmd = [
        str(CHROME),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={w},{h}",
        "--virtual-time-budget=6000",
        f"--screenshot={png}",
        f"file://{html}#png",
    ]
    # 크롬이 캡처를 저장한 뒤 종료 단계에서 가끔 실패한다(Teardown watchdog). 새 파일이 생겼으면 성공, 아니면 한 번 더
    last_err = ""
    for _ in range(2):
        start = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if png.exists() and png.stat().st_mtime >= start - 1:
            return png
        last_err = f"rc={res.returncode} stderr={res.stderr[-400:]}"
    raise RuntimeError(f"크롬 캡처 실패(2번): html={html} {last_err}")


def _fmt(v: float, digits: int) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.{digits}f}"


def caption(payload: dict[str, object]) -> str:
    """텔레그램 사진 설명 글(짧게)."""
    m = payload["meta"]
    p = payload["panel"]
    assert isinstance(m, dict) and isinstance(p, dict)
    d = int(m["digits"])
    lines = [
        f"{_fmt(float(m['close']), d)} ({float(m['change_pct']):+.2f}%) · {m['date']} 종가",
        f"점검표: {p['checklist_summary']}",
    ]
    o = p.get("odds")
    if isinstance(o, dict):
        if o["score"] is None:
            head = f"추세 상태 {o['label']}"
        elif o.get("score_name"):
            head = f"기술적 점수 {o['score'] * 100:.0f}({o['score_label']}, {'상승장' if o['bull'] else '하락장'}·{o['score_name']})"
        else:
            head = f"기술적 점수 {o['score'] * 100:.0f}({o['score_label']})"
        beat = f" · 코스피보다 나음 {o['beat'] * 100:.0f}%" if o["beat"] is not None else ""
        base_beat = f"·{o['base_beat'] * 100:.0f}%" if o["base_beat"] is not None else ""
        lines.append(
            f"{head} → 20거래일 뒤 상승 {o['up'] * 100:.0f}% · 하락 {o['down'] * 100:.0f}%{beat}"
            f" (전체 {o['base_up'] * 100:.0f}%·{o['base_down'] * 100:.0f}%{base_beat}, 과거 빈도)"
        )
    combo = p.get("combo")
    if isinstance(combo, dict):
        pn = combo["panic"]
        parts = []
        if combo.get("pullback"):
            pb = combo["pullback"]
            parts.append(f"눌림목 점수 {pb['score'] * 100:.0f}({pb['label']})")
        for key, title in (("high52", "52주 고점 근접"), ("composite", "가격 기반 종합")):
            o = combo.get(key)
            if o and o["score"] == o["score"]:
                parts.append(f"{title} {o['score'] * 100:.0f}({o['label']})")
        parts.append(f"시장 RSI<30 비율 {pn['fraction'] * 100:.0f}%{' 투매일' if pn['is_panic'] else ''}")
        lines.append("묶음: " + " · ".join(parts))
    combo_m = p.get("combo", {}).get("market") if isinstance(p.get("combo"), dict) else None
    tm = combo_m.get("temp") if isinstance(combo_m, dict) else None
    if isinstance(tm, dict) and isinstance(tm.get("score"), float) and math.isfinite(tm["score"]):
        fq = tm.get("freq")
        tail = f" · 같은 구간 20일 뒤 코스피 상승 2010–15 {fq['S1'] * 100:.0f}% / 2020–26 {fq['S2'] * 100:.0f}%" if fq else ""
        lines.append(f"시장 체온 {tm['score']:.0f}/100({tm['label']}){tail} — 기간마다 방향이 반대(판정 무의미)")
    av = p.get("avoid")
    if isinstance(av, list) and av:
        seen: dict[str, list[dict[str, object]]] = {}
        for x in av:
            seen.setdefault(str(x["kind"]), []).append(x)
        lines.append(
            "피해야 할 상태(20일): "
            + ", ".join(f"{xs[0]['name']}{f' ×{len(xs)}' if len(xs) > 1 else ''} {str(xs[0]['date'])[5:]}" for xs in seen.values())
        )
    pats = p.get("patterns")
    if isinstance(pats, list) and pats:
        lines.append(
            "차트 패턴(20일): "
            + ", ".join(f"{x['name']} {str(x['date'])[5:]}({x['score']['verdict'] if x['score'] else '미측정'})" for x in pats[:4])
        )
    fired = p["fired"]
    if fired:
        lines.append("오늘 신호: " + ", ".join(f["text"] for f in fired))
    for pos in p["positions"][:2]:
        lines.append(f"{pos['label']} 레벨 {pos['ratio']} ({pos['below']} ~ {pos['above']})")
    if p["near_above"]:
        a = p["near_above"][0]
        lines.append(f"위 {a['value']} ({a['pct']:+.1f}%, {a['labels']})")
    if p["near_below"]:
        b = p["near_below"][0]
        lines.append(f"아래 {b['value']} ({b['pct']:+.1f}%, {b['labels']})")
    if m.get("scorecard"):
        lines.append("예측 점수가 아니라 지금 상태. 실측 = 2010–15·2020–26 과거 성적(사전등록 판정).")
    else:
        lines.append("예측 점수가 아니라 지금 상태. 점검표 실측은 연구 결과 뒤 채워짐.")
    # 텔레그램 HTML 은 <b> 말고 < > & 를 엔티티로 받는다(RSI<30 · %K>%D 가 태그로 읽혀 발송이 거절됐다)
    body = [escape(s, quote=False) for s in lines]
    body[0] = f"<b>{escape(str(m['name']), quote=False)}</b> {body[0]}"
    return "\n".join(body)
