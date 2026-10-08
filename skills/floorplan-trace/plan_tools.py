"""평면도 측정과 자료 준비 도구. 좌표는 원본 이미지 픽셀(x 오른쪽, y 아래).

PIL·numpy를 쓰며 PDF 명령은 PyMuPDF가 필요하다. Blender 밖 Python에서도 불러 쓸 수 있다.

명령줄:
  python plan_tools.py survey <plan.pdf|png|jpg> [--pages 1,3] [--dpi 150] [--out profile.json]
  python plan_tools.py render <plan.pdf> <out.png> --page 1 [--dpi 300] [--clip x0 y0 x1 y1] [--drop-text '<정규식>']
  python plan_tools.py pdfbars <plan.pdf> [--page 1]
  python plan_tools.py pdfdims <plan.pdf> [--page 1]
  python plan_tools.py tiles <plan> <out_dir> [--tile 260] [--scale 3] [--step 10]
  python plan_tools.py crop <plan> <out.png> x0 y0 x1 y1 [--scale 3] [--step 10]
  python plan_tools.py runs <plan> row|col <index> <from> <to> [--band 0] [--min-len 1] [벽 색 인자]
  python plan_tools.py arc <plan> <x0> <x1> [--side top|bottom|left|right] [--step 2] [--profile p.json]
  python plan_tools.py scale <evidence.json> [--tol 0.03]
  python plan_tools.py align <base.png> <other.png> --core x0 y0 x1 y1 [--search 150] [벽 색 인자]
  python plan_tools.py fitalign <pairs.json> [--out fit.json]
  python plan_tools.py suite [--tests <dir>] [--out <dir>] [--no-render]
  python plan_tools.py selftest [--out <dir>]

벽 색 인자: --profile <계열 프로필.json>, --wall-rgb r,g,b [--wall-tol 24], --dark-sum 300.
계열별 값은 profiles/*.json에 둔다.
fitalign JSON: {"x": [[a, b], ...], "y": [[a, b], ...]}; a = s*b + d.
쌍마다 같은 가중치, 두 축에 공통 배율, 축별 이동. 잔차는 a - (s*b + d), 픽셀 단위.
"""
import argparse
import json
import math
import os
import re
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DARK_SUM = 300  # RGB 합이 이보다 작으면 벽 띠(검정)로 본다.

NONWHITE_SUM = 600  # 외곽선 추출용: 흰 바탕이 아닌 픽셀.

TEXT_PATTERNS = {
    'ft_in': r"\d+\s*['’′]\s*-?\s*\d+(?:\.\d+)?\s*(?:\"|”|″|'')",
    'ft_dims': r"\d+\s*['’′]\s*-?\s*\d*\s*(?:\"|”|″)?\s*[xX×]\s*\d+\s*['’′]",
    'metric_dims': r'\b\d{1,2}[.,]\d{1,2}\s*[xX×]\s*\d{1,2}[.,]\d{1,2}\b',
    'mm_dims': r'(?m)^\s*\d{2,4}0\s*(?:mm)?\s*$',
    'sq_ft': r'(?i)\bsq\.?\s*ft\b|\bsq\.?ft\b|square\s+feet|\bsf\b',
    'sq_m': r'(?i)\bsq\.?\s*m\b|m²|\bm2\b|\bsqm\b',
    'area_table': r'(?i)total\s+area|suite\s+area|internal\s+area|interiors?\s*:|balcony\s+area|terrace',
    'scale_word': r'(?i)\bscale\b|\b1\s*:\s*\d{2,3}\b',
    'ceiling_height': r'(?i)ceiling|clear\s+height',
    'north': r'(?i)\bnorth\b|^N$',
    'watermark_url': r'(?i)www\.[a-z0-9-]+\.[a-z]+',
}

FT_IN = r"(\d+)\s*'\s*-?\s*(\d+(?:\.\d+)?)?\s*\"?"

DIM_PAIR = re.compile(FT_IN + r"\s*[xX×]?\s*" + FT_IN)

M_PAIR = re.compile(r'\b(\d{1,2}[.,]\d{1,2})\s*[xX×]\s*(\d{1,2}[.,]\d{1,2})\b')

MM_DIM = re.compile(r'^\s*(\d{3,5})\s*(?:mm)?\s*$', re.I)

FT = 0.3048

DEFAULT_SCALE_TOL = {'bar': 0.02, 'bar_box': 0.02, 'dim': 0.03, 'area': 0.05, 'pt': 0.01, 'fixture': 0.15}

PX_QUANT = 2.0  # 길이 근거의 픽셀 측정 오차(양 끝 ±1px). 허용 오차는 max(종류별 오차, PX_QUANT/px)

ANISOTROPY_TOL = 0.04  # 가로·세로 축척 중앙값 차이가 이보다 크면 비등방으로 본다

WEAK_SCALE_KINDS = ('fixture',)  # 관례 치수(현관문 폭, 침대 길이 등). 대조에는 쓰지만 통과 조건의 근거 종류로 세지 않는다


def load(path):
    """원본 이미지와 RGB 합 배열(int)을 돌려준다."""
    im = Image.open(path).convert('RGB')
    return im, np.asarray(im).astype(np.int32).sum(2)



def dark_mask(rgb_sum, threshold=DARK_SUM):
    return rgb_sum < threshold



def load_rgb(path):
    """원본 이미지와 HxWx3 uint8 배열."""
    im = Image.open(path).convert('RGB')
    return im, np.asarray(im)



def load_profile(path):
    """계열 프로필(JSON). 상대 경로면 이 파일 옆 profiles/에서도 찾는다."""
    if not os.path.isfile(path):
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')
        for cand in (path + '.json', os.path.join(here, path), os.path.join(here, path + '.json')):
            if os.path.isfile(cand):
                path = cand
                break
    with open(path, encoding='utf-8') as fp:
        return json.load(fp)



def _rgb255(c):
    """(0~1 실수 | 0~255 정수 | '#rrggbb') → [r, g, b] 정수."""
    if isinstance(c, str):
        c = c.lstrip('#')
        return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    c = list(c)
    if all(isinstance(v, float) for v in c) and max(c) <= 1.0:
        return [int(round(v * 255)) for v in c]
    return [int(v) for v in c]



def wall_spec(profile=None, wall_rgb=None, wall_tol=None, dark_sum=None):
    """벽 띠를 고르는 규칙. {'rgb': [[r,g,b],...], 'tol': n} 또는 {'dark_sum': n}.
    profile(dict 또는 경로)의 'wall'을 기본으로 하고, 명령줄 값이 있으면 덮어쓴다."""
    spec = {}
    if profile:
        p = load_profile(profile) if isinstance(profile, str) else profile
        spec.update(p.get('wall', {}))
    if wall_rgb:
        spec['rgb'] = [_rgb255(c) for c in wall_rgb]
        spec.pop('dark_sum', None)
    if wall_tol is not None:
        spec['tol'] = wall_tol
    if dark_sum is not None:
        spec['dark_sum'] = dark_sum
    if 'rgb' in spec:
        spec['rgb'] = [_rgb255(c) for c in spec['rgb']]
        spec.setdefault('tol', 24)
    if not spec:
        spec = {'dark_sum': DARK_SUM}
    return spec



def wall_mask(arr, spec=None):
    """HxWx3 배열에서 벽 띠 픽셀. spec의 rgb 색(채널별 차이 ≤ tol) 또는 RGB 합 < dark_sum.
    둘 다 있으면 합집합이다(검정 외벽 + 회색 내벽 같은 도면)."""
    spec = spec or {'dark_sum': DARK_SUM}
    a = np.asarray(arr)
    m = np.zeros(a.shape[:2], bool)
    if 'rgb' in spec:
        a16 = a.astype(np.int16)
        for c in spec['rgb']:
            m |= np.abs(a16 - np.asarray(c, np.int16)).max(2) <= spec.get('tol', 24)
    if 'dark_sum' in spec or 'rgb' not in spec:
        m |= a.astype(np.int32).sum(2) < spec.get('dark_sum', DARK_SUM)
    return m



def background_mask(arr, profile=None, tol=None):
    """바탕 픽셀. 프로필의 background_rgb(없으면 흰색)와 채널별 차이 ≤ tol."""
    p = (load_profile(profile) if isinstance(profile, str) else profile) or {}
    bg = _rgb255(p.get('background_rgb', [255, 255, 255]))
    t = tol if tol is not None else p.get('background_tol', 20)
    return np.abs(np.asarray(arr).astype(np.int16) - np.asarray(bg, np.int16)).max(2) <= t



def _runs(values, offset):
    out, cur = [], None
    for i, v in enumerate(values):
        if v:
            if cur is None:
                cur = [i + offset, i + offset]
            cur[1] = i + offset
        elif cur is not None:
            out.append(tuple(cur))
            cur = None
    if cur is not None:
        out.append(tuple(cur))
    return out



def runs_row(mask, y, x0, x1, band=0, min_len=1):
    """y 행에서 x0..x1 사이 어두운 구간 [(시작, 끝)].
    band>0이면 y±band 모든 행이 어두운 곳만(행 방향으로 짧은 점·글자 제외),
    min_len은 구간 폭 하한(벽 띠는 보통 5~8px, 가구·문짝 선은 1~2px)."""
    rows = mask[max(0, y - band):y + band + 1, x0:x1]
    return [r for r in _runs(rows.all(0), x0) if r[1] - r[0] + 1 >= min_len]



def runs_col(mask, x, y0, y1, band=0, min_len=1):
    cols = mask[y0:y1, max(0, x - band):x + band + 1]
    return [r for r in _runs(cols.all(1), y0) if r[1] - r[0] + 1 >= min_len]



def edge_points(rgb_sum, lo, hi, side='top', step=2, threshold=NONWHITE_SUM, mask=None):
    """lo..hi 범위의 각 열(top/bottom) 또는 행(left/right)에서 바깥쪽 첫 비백색 픽셀.
    바탕이 흰색이 아닌 도면은 mask(바탕이 아닌 픽셀, 예: ~background_mask)를 넘긴다."""
    if mask is None:
        mask = rgb_sum < threshold
    pts = []
    for i in range(lo, hi, step):
        line = mask[:, i] if side in ('top', 'bottom') else mask[i, :]
        idx = np.where(line)[0]
        if not len(idx):
            continue
        j = idx[0] if side in ('top', 'left') else idx[-1]
        pts.append((i, int(j)) if side in ('top', 'bottom') else (int(j), i))
    return np.array(pts, float)



def fit_circle(points):
    """대수적 최소제곱 원 맞춤 → (cx, cy, r, 최대 잔차, 잔차 표준편차)."""
    p = np.asarray(points, float)
    a = np.c_[2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))]
    b = (p ** 2).sum(1)
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    r = math.sqrt(c + cx * cx + cy * cy)
    res = np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r
    return float(cx), float(cy), float(r), float(abs(res).max()), float(res.std())



def angles_about(center, points):
    """중심에서 본 각도(도)와 이웃 간격. 멀리언·기둥이 등간격인지 확인할 때 쓴다."""
    cx, cy = center
    ang = [math.degrees(math.atan2(y - cy, x - cx)) for x, y in points]
    return ang, list(np.diff(ang))



def px_to_m(p, origin_px, scale):
    """이미지 픽셀 → 미터(x 오른쪽, y 위쪽)."""
    return ((p[0] - origin_px[0]) * scale, (origin_px[1] - p[1]) * scale)



def _font(size):
    for name in ('DejaVuSans.ttf', 'arial.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()



def grid_crop(plan, out, box, scale=3, step=10, major=50):
    """box=(x0,y0,x1,y1) 영역을 scale배 확대하고 step px 격자와 major px마다 좌표 숫자를 그린다.
    숫자는 원본 픽셀 좌표라 크롭을 보고 바로 좌표를 읽을 수 있다."""
    im = plan if isinstance(plan, Image.Image) else Image.open(plan).convert('RGB')
    x0, y0, x1, y1 = [int(v) for v in box]
    x1, y1 = min(x1, im.width), min(y1, im.height)
    c = im.crop((x0, y0, x1, y1)).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.NEAREST)
    d = ImageDraw.Draw(c)
    f = _font(11)
    for x in range((x0 // step + 1) * step, x1, step):
        u = (x - x0) * scale
        d.line([(u, 0), (u, c.height)], fill=(255, 0, 0) if x % major == 0 else (0, 200, 255), width=1)
        if x % major == 0:
            d.text((u + 2, 1), str(x), fill=(255, 0, 0), font=f)
    for y in range((y0 // step + 1) * step, y1, step):
        v = (y - y0) * scale
        d.line([(0, v), (c.width, v)], fill=(255, 0, 0) if y % major == 0 else (0, 200, 255), width=1)
        if y % major == 0:
            d.text((2, v + 1), str(y), fill=(255, 0, 0), font=f)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    c.save(out)
    return out



def tiles(plan, out_dir, tile=260, overlap=30, scale=3, step=10):
    """평면 전체를 겹치는 격자 크롭으로 나누고, 50px 격자 전체 개관도 한 장 만든다."""
    im = Image.open(plan).convert('RGB')
    os.makedirs(out_dir, exist_ok=True)
    made = [grid_crop(im, os.path.join(out_dir, 'overview.png'), (0, 0, im.width, im.height), 2, 50, 100)]
    xs = list(range(0, max(1, im.width - overlap), tile - overlap))
    ys = list(range(0, max(1, im.height - overlap), tile - overlap))
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            made.append(grid_crop(im, os.path.join(out_dir, f'tile_r{j}c{i}_{x}_{y}.png'),
                                  (x, y, x + tile, y + tile), scale, step))
    return made



def _label(mask):
    """4-연결 성분 라벨. scipy가 있으면 그것을 쓴다."""
    try:
        from scipy import ndimage
        lab, n = ndimage.label(mask)
        return lab, n
    except ImportError:
        pass
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    flat_mask = mask.ravel()
    flat = lab.ravel()
    n = 0
    for start in np.flatnonzero(flat_mask):
        if flat[start]:
            continue
        n += 1
        flat[start] = n
        q = deque([start])
        while q:
            k = q.popleft()
            y, x = divmod(k, w)
            for nk, ok in ((k - 1, x > 0), (k + 1, x < w - 1), (k - w, y > 0), (k + w, y < h - 1)):
                if ok and flat_mask[nk] and not flat[nk]:
                    flat[nk] = n
                    q.append(nk)
    return lab, n



def _erode(mask, r=1):
    m = mask.copy()
    for _ in range(r):
        e = m.copy()
        e[1:, :] &= m[:-1, :]
        e[:-1, :] &= m[1:, :]
        e[:, 1:] &= m[:, :-1]
        e[:, :-1] &= m[:, 1:]
        m = e
    return m



def _dilate(mask, r=1):
    m = mask.copy()
    for _ in range(r):
        e = m.copy()
        e[1:, :] |= m[:-1, :]
        e[:-1, :] |= m[1:, :]
        e[:, 1:] |= m[:, :-1]
        e[:, :-1] |= m[:, 1:]
        m = e
    return m



def _resolve(path, base):
    """상대 경로를 현재 폴더 → base → base의 상위 폴더들 순서로 찾는다(저장소 루트의 refs/ 등)."""
    if not path or os.path.isabs(path) or os.path.exists(path):
        return path
    d = base
    while True:
        cand = os.path.join(d, path)
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 아직 없는 파일(렌더링할 대상)은 저장소 루트(.git이 있는 상위 폴더) 기준, 없으면 base 기준
    d = base
    while True:
        if os.path.exists(os.path.join(d, '.git')):
            return os.path.join(d, path)
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.join(base, path)
        d = parent



def _fitz():
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            raise SystemExit('PDF를 다루려면 PyMuPDF가 필요하다: pip install pymupdf')
    return fitz



def drop_text_spans(page, pattern):
    """글자가 정규식 pattern에 맞는 텍스트 span을 지운다(도형·이미지는 그대로). 지운 span 수를 돌려준다.
    워터마크가 텍스트로 들어 있고 벽과 비슷한 회색으로 렌더링될 때 쓴다(애스턴마틴: 투명도 20% 검정 Arial 50pt).
    글자 단위 상자로 지우므로 워터마크 글자와 겹친 다른 글자(방 이름 등)도 함께 사라질 수 있다."""
    fitz = _fitz()
    rx = re.compile(pattern)
    n = 0
    for b in page.get_text('rawdict')['blocks']:
        for line in b.get('lines', []):
            for sp in line['spans']:
                text = ''.join(c['c'] for c in sp['chars'])
                if not rx.search(text):
                    continue
                n += 1
                for c in sp['chars']:
                    page.add_redact_annot(fitz.Rect(c['bbox']))
    if n:
        page.apply_redactions(images=0, graphics=0)
    return n



def render_pdf(pdf, out, page=1, dpi=300, clip=None, drop_text=None):
    """PDF 한 페이지를 PNG로 렌더링한다. page는 1부터, clip은 pt 단위 (x0, y0, x1, y1).
    <out>.json에 pt↔px 변환 정보(px_per_pt, clip_pt)를 남겨 벡터 좌표·축척을 픽셀로 옮길 수 있게 한다.
    겹쳐 칠해 가려진 도형은 렌더링에 나타나지 않으므로, 해석은 항상 이 이미지로 한다.
    drop_text(정규식)를 주면 맞는 텍스트 span(워터마크)을 지운 뒤 렌더링한다(원본 파일은 바꾸지 않음)."""
    fitz = _fitz()
    d = fitz.open(pdf)
    p = d[page - 1]
    dropped = drop_text_spans(p, drop_text) if drop_text else 0
    z = dpi / 72.0
    rect = fitz.Rect(*clip) if clip else p.rect
    pix = p.get_pixmap(matrix=fitz.Matrix(z, z), clip=rect, alpha=False)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    pix.save(out)
    meta = {'pdf': os.path.abspath(pdf), 'page': page, 'dpi': dpi, 'px_per_pt': z,
            'clip_pt': [round(v, 3) for v in (rect.x0, rect.y0, rect.x1, rect.y1)],
            'page_size_pt': [round(p.rect.width, 3), round(p.rect.height, 3)], 'rotation': p.rotation,
            'size_px': [pix.width, pix.height], 'drop_text': drop_text, 'dropped_spans': dropped}
    with open(out + '.json', 'w', encoding='utf-8') as fp:
        json.dump(meta, fp, indent=2)
    return meta



def render_meta(png):
    """render_pdf가 남긴 <png>.json. 없으면 None."""
    path = png + '.json'
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fp:
            return json.load(fp)
    return None



def pt_to_px(meta, x, y):
    z = meta['px_per_pt']
    return ((x - meta['clip_pt'][0]) * z, (y - meta['clip_pt'][1]) * z)



def px_to_pt(meta, x, y):
    z = meta['px_per_pt']
    return (meta['clip_pt'][0] + x / z, meta['clip_pt'][1] + y / z)



def pdf_fill_stats(page):
    """채움 색별 경로 수와 경계 상자 면적(pt²). 벽 색 후보를 고르는 근거로만 쓴다(겹침은 고려하지 않음)."""
    stats = {}
    for path in page.get_drawings():
        if path.get('fill') is None:
            continue
        c = tuple(_rgb255([float(v) for v in path['fill']]))
        s = stats.setdefault(c, {'rgb': list(c), 'paths': 0, 'bbox_area_pt2': 0.0})
        s['paths'] += 1
        s['bbox_area_pt2'] += path['rect'].width * path['rect'].height
    return sorted(stats.values(), key=lambda s: -s['bbox_area_pt2'])



def pdf_color_mask(page, rgb, dpi=150, clip=None, tol=3):
    """채움 색이 rgb(±tol)인 벡터 경로만 새 페이지에 다시 그려 렌더링한 마스크.
    원본 렌더링과 비교하면 나중에 칠한 색에 가려진 비율을 잴 수 있다(pdf_hidden_ratio)."""
    fitz = _fitz()
    rgb = _rgb255(rgb)
    doc = fitz.open()
    np_ = doc.new_page(width=page.rect.width, height=page.rect.height)
    shape = np_.new_shape()
    n = 0
    for path in page.get_drawings():
        f = path.get('fill')
        if f is None or max(abs(a - b) for a, b in zip(_rgb255([float(v) for v in f]), rgb)) > tol:
            continue
        for it in path['items']:
            if it[0] == 'l':
                shape.draw_line(it[1], it[2])
            elif it[0] == 're':
                shape.draw_rect(it[1])
            elif it[0] == 'qu':
                shape.draw_quad(it[1])
            elif it[0] == 'c':
                shape.draw_bezier(it[1], it[2], it[3], it[4])
        shape.finish(fill=(0, 0, 0), color=None, even_odd=bool(path.get('even_odd')),
                     closePath=bool(path.get('closePath', True)))
        n += 1
    shape.commit()
    z = dpi / 72.0
    pix = np_.get_pixmap(matrix=fitz.Matrix(z, z), clip=fitz.Rect(*clip) if clip else page.rect, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
    return a.sum(2) < 384, n



def _render_array(page, dpi, clip=None):
    fitz = _fitz()
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), clip=fitz.Rect(*clip) if clip else page.rect, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]



def pdf_hidden_ratio(page, rgb, dpi=150, render=None, tol_render=30):
    """그 색으로 칠한 벡터 도형 중 렌더링에서 실제로 그 색으로 보이는 비율(visible)과 가려진 비율(hidden).
    애스턴마틴처럼 벽 회색이 계단·기둥·지붕에도 쓰이고 뒤에 칠한 색이 덮는 도면에서는 hidden이 크다.
    그럴 때 벡터 도형을 벽으로 믿지 말고 렌더링 이미지로 해석한다."""
    mask, n = pdf_color_mask(page, rgb, dpi)
    arr = render if render is not None else _render_array(page, dpi)
    inner = _erode(mask, 1)  # 안티앨리어싱 가장자리는 뺀다
    total = int(inner.sum())
    if not total:
        return {'rgb': _rgb255(rgb), 'paths': n, 'visible': None, 'hidden': None}
    seen = np.abs(arr.astype(np.int16) - np.asarray(_rgb255(rgb), np.int16)).max(2) <= tol_render
    vis = float((inner & seen).sum()) / total
    return {'rgb': _rgb255(rgb), 'paths': n, 'visible': round(vis, 3), 'hidden': round(1 - vis, 3)}



def pdf_scale_bars(page, max_h=10.0, min_len=15.0, aspect=3.0):
    """축척 막대 후보: 높이가 낮고 가로로 긴 사각형(채움·선)이 같은 줄에 붙어 이어진 것.
    길이(pt)와 주변 글자(숫자, ft, m)를 돌려준다. 어느 것이 막대이고 몇 ft인지는 사람이 렌더링을 보고 정한다."""
    rects = []
    for path in page.get_drawings():
        for it in path['items']:
            if it[0] != 're':
                continue
            r = it[1]
            if 0 < r.height <= max_h and r.width >= aspect * r.height:
                rects.append((r, path.get('fill')))
    rects.sort(key=lambda t: (round((t[0].y0 + t[0].y1) / 2), t[0].x0))
    bars, cur = [], None
    for r, fill in rects:
        yc = (r.y0 + r.y1) / 2
        if cur and abs(yc - cur['yc']) < 1.0 and r.x0 <= cur['x1'] + 2.0 and abs(r.height - cur['h']) < 1.0:
            cur['x1'] = max(cur['x1'], r.x1)
            cur['segments'].append(round(r.width, 3))
            cur['fills'].add(tuple(_rgb255([float(v) for v in fill])) if fill else None)
        else:
            cur = {'yc': yc, 'x0': r.x0, 'x1': r.x1, 'h': r.height, 'segments': [round(r.width, 3)],
                   'fills': {tuple(_rgb255([float(v) for v in fill])) if fill else None}}
            bars.append(cur)
    # 체커보드 막대(칸이 두 줄로 엇갈림)는 위아래로 맞닿거나 같은 높이로 옆에 붙은 묶음을 합친다(바뀌지 않을 때까지)
    def touch(m, b):
        xa = b['x0'] <= m['x1'] + 2.0 and m['x0'] <= b['x1'] + 2.0
        mt, mb, bt, bb = m['yc'] - m['h'] / 2, m['yc'] + m['h'] / 2, b['yc'] - b['h'] / 2, b['yc'] + b['h'] / 2
        stacked = abs(mb - bt) < 1.0 or abs(bb - mt) < 1.0
        same_row = abs(mt - bt) < 1.0 and abs(mb - bb) < 1.0
        return xa and (stacked or same_row)
    changed = True
    while changed:
        changed = False
        for i in range(len(bars)):
            for j in range(i + 1, len(bars)):
                m, b = bars[i], bars[j]
                if not touch(m, b):
                    continue
                top = min(m['yc'] - m['h'] / 2, b['yc'] - b['h'] / 2)
                bot = max(m['yc'] + m['h'] / 2, b['yc'] + b['h'] / 2)
                if bot - top > max_h * 2.5:
                    continue
                stacked = abs(m['yc'] - b['yc']) > 0.5
                m['x0'], m['x1'] = min(m['x0'], b['x0']), max(m['x1'], b['x1'])
                m['segments'] = sorted(m['segments'] + b['segments'])
                m['fills'] |= b['fills']
                m['yc'], m['h'] = (top + bot) / 2, bot - top
                m['checker'] = m.get('checker', False) or b.get('checker', False) or stacked
                del bars[j]
                changed = True
                break
            if changed:
                break
    words = page.get_text('words')
    out = []
    for b in bars:
        length = b['x1'] - b['x0']
        if length < min_len:
            continue
        box = (b['x0'] - 25, b['yc'] - b['h'] / 2 - 30, b['x1'] + 40, b['yc'] + b['h'] / 2 + 30)
        labels = [w[4] for w in words if w[0] >= box[0] and w[2] <= box[2] and w[1] >= box[1] and w[3] <= box[3]
                  and re.search(r"\d|ft|feet|m\b|'", w[4], re.I)]
        out.append({'bbox_pt': [round(b['x0'], 2), round(b['yc'] - b['h'] / 2, 2), round(b['x1'], 2),
                                round(b['yc'] + b['h'] / 2, 2)],
                    'length_pt': round(length, 3), 'segments_pt': b['segments'],
                    'alternating_fill': len(b['fills']) > 1 or b.get('checker', False), 'labels': labels})
    # 라벨이 있고 칸이 여러 개인 것을 앞에
    out.sort(key=lambda b: (-(bool(b['labels'])), -(len(b['segments_pt']) > 1), -b['length_pt']))
    return out



def normalize_quotes(text):
    """타이포 따옴표를 ASCII로: ’’/″/” → \", ’/′ → '."""
    return (text.replace('’’', '"').replace("''", '"').replace('″', '"').replace('”', '"')
            .replace('’', "'").replace('′', "'"))



def pdf_dims(page):
    """방 치수 쌍과 단독 mm 치수 후보. bbox_pt는 회전을 적용한 렌더링 페이지 좌표(pt).
    단독 숫자는 끝이 0인 3~5자리만 후보로 낸다. 방 번호/표 숫자인지는 원본에서 확인한다."""
    out = []
    for b in page.get_text('dict')['blocks']:
        for line in b.get('lines', []):
            text = normalize_quotes(' '.join(sp['text'] for sp in line['spans']))
            bbox = _fitz().Rect(line['bbox']) * page.rotation_matrix
            box = [round(v, 3) for v in bbox]
            for m in DIM_PAIR.finditer(text):
                a = parse_length(f"{m.group(1)}'{m.group(2) or 0}\"")
                c = parse_length(f"{m.group(3)}'{m.group(4) or 0}\"")
                out.append({'text': m.group(0).strip(), 'm': [round(a, 4), round(c, 4)],
                            'bbox_pt': box, 'unit': 'ft-in'})
            for m in M_PAIR.finditer(text):
                out.append({'text': m.group(0), 'm': [float(m.group(1).replace(',', '.')), float(m.group(2).replace(',', '.'))],
                            'bbox_pt': box, 'unit': 'm'})
            for sp in line['spans']:
                m = MM_DIM.fullmatch(sp['text'])
                if m and m.group(1).endswith('0'):
                    bbox = _fitz().Rect(sp['bbox']) * page.rotation_matrix
                    out.append({'text': sp['text'].strip(), 'm': [int(m.group(1)) / 1000.0], 'unit': 'mm',
                                'bbox_pt': [round(v, 3) for v in bbox], 'candidate': True})
    return out



def text_evidence(text):
    """페이지 글자에서 단위·축척 근거가 될 패턴을 센다(예시 3개씩)."""
    text = normalize_quotes(text)
    text = re.sub(r'\d+\s*°\s*\d+\s*\'\s*\d+(?:\.\d+)?\s*"', '', text)  # 방위각의 분·초는 ft-in이 아니다
    out = {}
    for k, pat in TEXT_PATTERNS.items():
        hits = re.findall(pat, text, re.M)
        if hits:
            out[k] = {'count': len(hits), 'examples': sorted(set(h if isinstance(h, str) else h[0] for h in hits))[:3]}
    return out



def color_classes(arr, n=8, tol=24, min_share=0.002):
    """많은 색부터 tol 안의 픽셀을 묶어 n개 색 무리를 만든다. [(rgb, mask)]"""
    a16 = arr.astype(np.int16)
    q = (arr // 16).astype(np.int32)
    key = (q[..., 0] << 8) | (q[..., 1] << 4) | q[..., 2]
    remaining = np.ones(key.shape, bool)
    total = key.size
    out = []
    for _ in range(n * 3):
        if len(out) >= n:
            break
        ks = key[remaining]
        if ks.size < total * min_share:
            break
        vals, counts = np.unique(ks, return_counts=True)
        k = vals[counts.argmax()]
        sel = (key == k) & remaining
        c = arr[sel].reshape(-1, 3).mean(0)
        m = (np.abs(a16 - c.astype(np.int16)).max(2) <= tol) & remaining
        remaining &= ~m
        if m.sum() >= total * min_share:
            out.append(([int(round(v)) for v in c], m))
    return out



def band_profile(mask, max_band_px=25):
    """마스크 중 띠(두께 3~max_band_px)인 부분의 양과 대표 두께.
    2px 닫힘 뒤 침식을 1회 하고 남은 픽셀 중 max_band_px/2회 안에 사라지는 것이 띠, 끝까지 남는 것은 큰 면(제목 패널,
    바닥 채움, 해칭 바탕)이다. 같은 색이 벽과 큰 면에 함께 쓰여도 띠만 따로 잰다.
    반환 (band_share, band_px): band_share는 전체 픽셀 대비 띠 픽셀 비율, band_px는 띠의 80%가 사라지는 두께."""
    k = max(2, max_band_px // 2)
    # 가구·글자의 가는 선이 바닥 채움을 잘게 나누면 띠처럼 보이므로 2px 틈을 먼저 메운다(닫힘 연산)
    m = _erode(_erode(_dilate(mask, 2), 2), 1)
    counts = [int(m.sum())]
    for _ in range(2, k + 1):
        m = _erode(m, 1)
        counts.append(int(m.sum()))
    band = counts[0] - counts[-1]
    if band <= 0:
        return 0.0, 0
    for i, c in enumerate(counts, 1):
        if counts[0] - c >= 0.8 * band:
            return band / mask.size, 2 * i + 1
    return band / mask.size, 2 * k + 1



def _components_bbox(mask, min_frac=0.02):
    lab, n = _label(mask)
    if not n:
        return []
    counts = np.bincount(lab.ravel())
    total = counts[1:].sum()
    out = []
    for k in np.flatnonzero(counts >= max(1, min_frac * total)):
        if k == 0:
            continue
        ys, xs = np.nonzero(lab == k)
        out.append([int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())])
    return out



def _mirror_iou(mask):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None, None
    m = _dilate(mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1], 2)
    inter_lr = (m & m[:, ::-1]).sum()
    inter_ud = (m & m[::-1, :]).sum()
    return round(float(inter_lr / ((m | m[:, ::-1]).sum() or 1)), 3), round(float(inter_ud / ((m | m[::-1, :]).sum() or 1)), 3)



def raster_survey(arr, max_band_px=25, n_colors=8):
    """렌더링(또는 래스터) 이미지에서 바탕색·벽 색 후보·평면 수·대칭을 추정한다."""
    classes = color_classes(arr, n_colors)
    bg = classes[0][0] if classes else [255, 255, 255]
    cands = []
    for rgb, m in classes[1:]:
        share, t = band_profile(m, max_band_px)
        cands.append({'rgb': rgb, 'share': round(float(m.mean()), 4), 'band_share': round(share, 4),
                      'band_px': t, 'band_like': share > 0.002 and t >= 3})
    walls = [c for c in cands if c['band_like']]
    # 벽은 바탕·바닥 채움보다 어둡다: 띠 양 × 바탕과의 명암 차. 바닥 채움(아틀란티스 황갈색)도 방마다
    # 테두리가 침식돼 띠처럼 잡히지만 명암 차가 작아 뒤로 밀린다.
    for c in walls:
        c['score'] = round(c['band_share'] * abs(sum(bg) - sum(c['rgb'])) / 765.0, 5)
    walls.sort(key=lambda c: -c['score'])
    out = {'background_rgb': bg, 'color_classes': cands, 'wall_candidates': walls}
    if walls:
        wm = np.zeros(arr.shape[:2], bool)
        a16 = arr.astype(np.int16)
        for c in walls[:1]:
            wm |= np.abs(a16 - np.asarray(c['rgb'], np.int16)).max(2) <= 24
        wm = _erode(wm, 1)
        big = _dilate(wm, max(3, int(0.01 * max(arr.shape[:2]))))
        out['plan_clusters_px'] = _components_bbox(big, 0.05)
        out['mirror_iou'] = dict(zip(('left_right', 'up_down'), _mirror_iou(wm)))
    return out



def survey(path, pages=None, dpi=None, max_side=2600):
    """계열 프로필 초안. 벡터/래스터 구성, 바탕·벽 색 후보, 가려진 비율, 축척 막대 후보, 단위·면적표 글자,
    한 페이지의 평면 수와 대칭을 모은다. 'family'와 'todo'는 사람이 채운다."""
    prof = {'family': '<계열 이름>', 'source': os.path.abspath(path), 'survey_version': 1, 'pages': []}
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        fitz = _fitz()
        d = fitz.open(path)
        prof['page_count'] = len(d)
        idx = pages or list(range(1, min(len(d), 3) + 1))
        fmts = []
        for pno in idx:
            p = d[pno - 1]
            drawings = p.get_drawings()
            imgs = p.get_image_info()
            img_area = sum(max(0, (min(i['bbox'][2], p.rect.x1) - max(i['bbox'][0], p.rect.x0))) *
                           max(0, (min(i['bbox'][3], p.rect.y1) - max(i['bbox'][1], p.rect.y0))) for i in imgs)
            cover = img_area / (p.rect.width * p.rect.height)
            fmt = 'raster_pdf' if cover > 0.25 and len(drawings) < 200 else ('mixed_pdf' if cover > 0.05 else 'vector_pdf')
            fmts.append(fmt)
            r_dpi = dpi or min(150, 72.0 * max_side / max(p.rect.width, p.rect.height))
            arr = _render_array(p, r_dpi)
            rs = raster_survey(arr)
            fills = pdf_fill_stats(p)[:6]
            for f in fills[:4]:
                if sum(f['rgb']) < 740:
                    f.update(pdf_hidden_ratio(p, f['rgb'], r_dpi, render=arr))
            text = p.get_text()
            pg = {'page': pno, 'size_pt': [round(p.rect.width, 1), round(p.rect.height, 1)], 'format': fmt,
                  'drawings': len(drawings), 'images': len(imgs), 'image_cover': round(cover, 3),
                  'render_dpi': round(r_dpi, 2), 'fills': fills, 'scale_bars': pdf_scale_bars(p)[:4],
                  'text': text_evidence(text), **rs}
            prof['pages'].append(pg)
        prof['format'] = max(set(fmts), key=fmts.count)
    else:
        _, arr = load_rgb(path)
        prof['format'] = 'raster_image'
        prof['pages'].append({'page': 1, 'size_px': [arr.shape[1], arr.shape[0]], 'format': 'raster_image',
                              **raster_survey(arr)})
    first = prof['pages'][0]
    prof['background_rgb'] = first['background_rgb']
    if first.get('wall_candidates'):
        prof['wall'] = {'rgb': [first['wall_candidates'][0]['rgb']], 'tol': 24}
    txt = {}
    for pg in prof['pages']:
        for k, v in pg.get('text', {}).items():
            txt[k] = txt.get(k, 0) + v['count']
    dims = ('ft-in' if txt.get('ft_in') or txt.get('ft_dims') else '') + \
        ('+metric' if txt.get('metric_dims') or txt.get('mm_dims') else '')
    area = '+'.join(u for u, k in (('sq ft', 'sq_ft'), ('sq m', 'sq_m')) if txt.get(k))
    prof['units'] = {'dims': dims.strip('+') or 'none', 'area': area or 'none'}
    ev = []
    if any(pg.get('scale_bars') and pg['scale_bars'][0]['labels'] for pg in prof['pages']):
        ev.append('bar(벡터 후보 있음, 렌더링에서 확인)')
    if txt.get('ft_dims') or txt.get('metric_dims'):
        ev.append('dims(방 치수 글자)')
    if txt.get('area_table') or txt.get('sq_ft') or txt.get('sq_m'):
        ev.append('area(면적표)')
    if txt.get('ceiling_height'):
        ev.append('ceiling_height(높이만, 평면 축척 근거 아님)')
    prof['scale_evidence_found'] = ev
    prof['quirks'] = []
    for pg in prof['pages']:
        for f in pg.get('fills', []):
            if f.get('hidden') and f['hidden'] > 0.1:
                prof['quirks'].append(f"p{pg['page']}: 채움 {f['rgb']}의 {f['hidden'] * 100:.0f}%가 렌더링에서 가려짐 → 벡터 도형을 벽으로 믿지 말 것")
        if pg.get('text', {}).get('watermark_url'):
            prof['quirks'].append(f"p{pg['page']}: 워터마크/URL 글자 {pg['text']['watermark_url']['examples']}")
        mi = pg.get('mirror_iou') or {}
        if max([v or 0 for v in mi.values()] or [0]) > 0.6:
            prof['quirks'].append(f"p{pg['page']}: 벽 배치가 대칭(IoU {mi}) → 한 페이지에 대칭 세대일 수 있음")
        if len(pg.get('plan_clusters_px', [])) > 1:
            prof['quirks'].append(f"p{pg['page']}: 평면 덩어리 {len(pg['plan_clusters_px'])}개(여러 평면/층)")
    prof['todo'] = ['family 이름', '벽 색 확인(렌더링 크롭에서 벽 띠 색을 runs로 재 본다)',
                    '축척 방법 결정과 scale 대조(근거 2종 이상)', '층별 위치가 다르면 align 결과 기록']
    return prof



def parse_length(s):
    """길이 문자열 → m. 12'-6\", 12' 6\", 12', 6\", 10 ft, 3.81 m, 3810 mm, 381 cm, 숫자(m)."""
    if isinstance(s, (int, float)):
        return float(s)
    t = s.strip().replace('’', "'").replace('′', "'").replace('”', '"').replace('″', '"').replace("''", '"')
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*'\s*-?\s*(?:(\d+(?:\.\d+)?)\s*(?:(\d+)/(\d+))?\s*\"?)?", t)
    if m:
        inch = float(m.group(2) or 0) + (float(m.group(3)) / float(m.group(4)) if m.group(3) else 0)
        return float(m.group(1)) * FT + inch * 0.0254
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*"', t)
    if m:
        return float(m.group(1)) * 0.0254
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(ft|feet|m|mm|cm|in)?', t, re.I)
    if m:
        f = {'ft': FT, 'feet': FT, 'm': 1.0, 'mm': 0.001, 'cm': 0.01, 'in': 0.0254, None: 1.0}[(m.group(2) or '').lower() or None]
        return float(m.group(1)) * f
    raise ValueError(f'길이를 읽을 수 없다: {s!r}')



def parse_area(s):
    """면적 문자열 → m². '795 sq ft', '73.9 sq m', '73.9 m2', '2,559 SQ.FT', 숫자(m²)."""
    if isinstance(s, (int, float)):
        return float(s)
    t = s.strip().replace(',', '')
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(.*)', t)
    if not m:
        raise ValueError(f'면적을 읽을 수 없다: {s!r}')
    v, u = float(m.group(1)), m.group(2).lower().replace(' ', '').replace('.', '')
    if u in ('', 'm2', 'm²', 'sqm', 'sqm2'):
        return v
    if u in ('sqft', 'sf', 'ft2', 'ft²', 'squarefeet'):
        return v * FT * FT
    raise ValueError(f'면적 단위를 모른다: {s!r}')



def _poly_area(pts):
    p = np.asarray(pts, float)
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))



def scale_estimate(evidence, base_dir='.', tol=None):
    """축척 근거 여러 개로 m/px를 추정하고 서로 대조한다.

    evidence = {'image': <png>(bar_box·pt에 필요), 'profile'/'wall': 벽 규칙,
                'items': [
                  {'kind': 'bar', 'p0': [x, y], 'p1': [x, y], 'length': "10'"},          # 막대 양 끝을 잰 픽셀
                  {'kind': 'bar_box', 'box': [x0, y0, x1, y1], 'length': "10'"},        # 상자 안 벽색 가로 범위
                  {'kind': 'dim', 'p0': .., 'p1': .., 'length': "12'-6\"", 'label': '거실 폭'},  # 벽면~벽면
                  {'kind': 'area', 'polygon': [[x, y], ...], 'area': '795 sq ft'},     # 면적표
                  {'kind': 'pt', 'm_per_pt': 0.0424},                                     # 벡터 축척 + 렌더 dpi
                  {'kind': 'fixture', 'p0': .., 'p1': .., 'length': 1.0, 'label': '현관문 폭(관례)'},  # 약한 근거
                ]}
    통과 조건: 모든 근거가 허용 오차 안이고, 약한 근거(fixture)를 뺀 근거 종류가 2개 이상이며,
    가로·세로 길이 근거가 각각 2개 이상이면 두 축의 축척이 ANISOTROPY_TOL 안에서 같다.
    길이 근거의 허용 오차는 픽셀 양자화(PX_QUANT/px)보다 작아지지 않는다.
    각 근거의 m/px, 중앙값, 중앙값 대비 오차, 근거 종류별 허용 오차 초과 여부를 돌려준다."""
    items = evidence['items']
    arr = mask = meta = None
    if evidence.get('image'):
        img = _resolve(evidence['image'], base_dir)
        meta = render_meta(img)
        if any(it['kind'] == 'bar_box' for it in items):
            _, arr = load_rgb(img)
            mask = wall_mask(arr, wall_spec(profile=evidence.get('profile') or ({'wall': evidence['wall']} if evidence.get('wall') else None)))
    rows = []
    for i, it in enumerate(items):
        k = it['kind']
        r = {'i': i, 'kind': k, 'label': it.get('label', '')}
        if k in ('bar', 'dim', 'fixture'):
            px = it.get('px') or math.hypot(it['p1'][0] - it['p0'][0], it['p1'][1] - it['p0'][1])
            m = parse_length(it['length'])
            r.update(px=round(px, 2), m=round(m, 4), m_per_px=m / px)
            if 'p0' in it:
                dx, dy = abs(it['p1'][0] - it['p0'][0]), abs(it['p1'][1] - it['p0'][1])
                r['axis'] = 'x' if dx > 5 * dy else 'y' if dy > 5 * dx else 'diag'
        elif k == 'bar_box':
            x0, y0, x1, y1 = it['box']
            cols = np.flatnonzero(mask[y0:y1, x0:x1].any(0))
            px = float(cols.max() - cols.min() + 1) if len(cols) else float('nan')
            m = parse_length(it['length'])
            r.update(px=px, m=round(m, 4), m_per_px=m / px)
        elif k == 'area':
            apx = it.get('px_area') or _poly_area(it['polygon'])
            m2 = parse_area(it['area'])
            r.update(px_area=round(apx, 1), m2=round(m2, 3), m_per_px=math.sqrt(m2 / apx))
        elif k == 'pt':
            z = it.get('px_per_pt') or (meta or {}).get('px_per_pt')
            if not z:
                raise ValueError("'pt' 근거에는 px_per_pt나 render 사이드카(<png>.json)가 필요하다")
            r.update(m_per_pt=it['m_per_pt'], px_per_pt=z, m_per_px=it['m_per_pt'] / z)
        else:
            raise ValueError(f'모르는 근거 종류: {k}')
        r['tol'] = it.get('tol', (tol or {}).get(k, DEFAULT_SCALE_TOL[k]) if isinstance(tol, dict) else (tol or DEFAULT_SCALE_TOL[k]))
        if 'px' in r and r['px'] > 0 and 'tol' not in it:
            # 양 끝을 ±1px로 재므로 짧은 길이는 픽셀 양자화만으로 오차가 커진다(26px면 ±7.7%)
            r['tol'] = round(max(r['tol'], PX_QUANT / r['px']), 4)
        rows.append(r)
    vals = np.array([r['m_per_px'] for r in rows], float)
    strong = np.array([r['m_per_px'] for r in rows if r['kind'] not in WEAK_SCALE_KINDS], float)
    med = float(np.median(strong if len(strong) else vals))  # 약한 근거는 대조만 하고 추정에는 넣지 않는다
    # 가로·세로 축척 비교: 웹 평면도는 틀에 맞춰 한 축만 늘이거나(비등방) 치수와 다르게 그린 것이 있다
    ax = {a: [r['m_per_px'] for r in rows if r.get('axis') == a and r['kind'] not in WEAK_SCALE_KINDS] for a in ('x', 'y')}
    axes = aniso = None
    if len(ax['x']) >= 2 and len(ax['y']) >= 2:
        mx, my = float(np.median(ax['x'])), float(np.median(ax['y']))
        axes = {'m_per_px_x': round(mx, 6), 'm_per_px_y': round(my, 6), 'x_over_y': round(mx / my, 4),
                'n_x': len(ax['x']), 'n_y': len(ax['y'])}
        if abs(mx / my - 1) > ANISOTROPY_TOL:
            aniso = {'x': mx, 'y': my}
            axes['geomean'] = round(math.sqrt(mx * my), 6)
    bad = []
    for r in rows:
        ref = aniso.get(r.get('axis'), med) if aniso else med  # 비등방이면 길이 근거는 자기 축의 중앙값과 비교
        r['deviation'] = round(r['m_per_px'] / ref - 1, 4)
        r['m_per_px'] = round(r['m_per_px'], 6)
        r['ok'] = abs(r['deviation']) <= r['tol']
        if not r['ok']:
            bad.append(r['i'])
    kinds = sorted({('bar' if r['kind'] == 'bar_box' else r['kind']) for r in rows if r['kind'] not in WEAK_SCALE_KINDS})
    out = {'m_per_px': round(med, 6), 'items': rows, 'kinds': kinds, 'max_abs_deviation': round(float(np.abs(vals / med - 1).max()), 4),
           'outliers': bad, 'passed': not bad and len(kinds) >= 2 and not aniso}
    if meta:
        out['m_per_pt'] = round(med * meta['px_per_pt'], 6)
    if axes:
        out['axes'] = axes
    if aniso:
        out['anisotropy_warning'] = (f"가로 {aniso['x']:.6f}·세로 {aniso['y']:.6f} m/px로 {abs(aniso['x'] / aniso['y'] - 1):.1%} 다르다. "
                                     '그림이 한 축으로 늘어났거나 치수대로 그려지지 않았다. 축척 하나로 모델링하지 말고, 축별 축척을 쓰거나 '
                                     '치수 글자를 기준으로 삼는다. 이때 길이 근거의 deviation은 자기 축 중앙값 기준이다.')
    if len(kinds) < 2:
        out['warning'] = (f'강한 근거 종류가 {len(kinds)}개뿐이다({kinds}). 다른 종류(막대/치수/면적/벡터 pt)로 대조해야 '
                          '통과로 본다. 도면에 그런 근거가 없으면 이 경고를 그대로 보고한다.')
    return out



def _xcorr(region, tmpl):
    """region 안에서 tmpl을 옮겨 가며 겹친 픽셀 수. 반환 [dy, dx] 배열(유효 위치만)."""
    H, W = region.shape
    h, w = tmpl.shape
    fr = np.fft.rfft2(region.astype(np.float32))
    ft = np.fft.rfft2(tmpl.astype(np.float32), s=(H, W))
    c = np.fft.irfft2(fr * np.conj(ft), s=(H, W))
    return c[:H - h + 1, :W - w + 1]



def align_floors(base_png, other_png, core, search=150, spec=None, out_png=None):
    """두 층 렌더링에서 코어(계단·승강기 벽처럼 층마다 같은 부분)를 기준으로 평행 이동을 찾는다.
    core는 base 이미지의 픽셀 상자. 결과 (dx, dy)는 base의 점이 other에서 놓이는 위치 - base 위치.
    두 이미지가 render_pdf 사이드카를 가지면 페이지 좌표(pt) 이동도 계산한다. 축척은 같다고 가정하고,
    맞춘 뒤 코어 IoU로 그 가정을 확인한다(IoU가 낮으면 축척·회전이 다르거나 코어 상자가 틀렸다)."""
    _, a = load_rgb(base_png)
    _, b = load_rgb(other_png)
    A = _erode(wall_mask(a, spec), 1)
    B = _erode(wall_mask(b, spec), 1)
    x0, y0, x1, y1 = [int(v) for v in core]
    T = A[y0:y1, x0:x1]
    s = int(search)
    R = np.zeros((y1 - y0 + 2 * s, x1 - x0 + 2 * s), bool)
    sy0, sx0 = max(0, y0 - s), max(0, x0 - s)
    sy1, sx1 = min(B.shape[0], y1 + s), min(B.shape[1], x1 + s)
    R[sy0 - (y0 - s):sy0 - (y0 - s) + (sy1 - sy0), sx0 - (x0 - s):sx0 - (x0 - s) + (sx1 - sx0)] = B[sy0:sy1, sx0:sx1]
    c = _xcorr(R, T)
    best = None
    flat = np.argsort(c.ravel())[::-1][:25]
    for k in flat:
        iy, ix = divmod(int(k), c.shape[1])
        win = R[iy:iy + T.shape[0], ix:ix + T.shape[1]]
        iou = float((win & T).sum()) / float((win | T).sum() or 1)
        if best is None or iou > best[0]:
            best = (iou, ix - s, iy - s)
    iou, dx, dy = best
    win0 = R[s:s + T.shape[0], s:s + T.shape[1]]
    iou0 = float((win0 & T).sum()) / float((win0 | T).sum() or 1)
    out = {'dx_px': dx, 'dy_px': dy, 'core_iou': round(iou, 3), 'core_iou_without_shift': round(iou0, 3),
           'core_px': [x0, y0, x1, y1], 'search_px': s, 'at_search_edge': max(abs(dx), abs(dy)) >= s - 1}
    ma, mb = render_meta(base_png), render_meta(other_png)
    if ma and mb:
        z = ma['px_per_pt']
        out['same_render_scale'] = abs(z - mb['px_per_pt']) < 1e-6
        out['dx_pt'] = round(mb['clip_pt'][0] - ma['clip_pt'][0] + dx / z, 3)
        out['dy_pt'] = round(mb['clip_pt'][1] - ma['clip_pt'][1] + dy / z, 3)
    if out_png:
        vis = np.full(A.shape + (3,), 255, np.uint8)
        vis[A] = (220, 40, 40)
        Bs = np.zeros_like(A)
        ys, xs = np.nonzero(B)
        ys, xs = ys - dy, xs - dx
        ok = (ys >= 0) & (ys < A.shape[0]) & (xs >= 0) & (xs < A.shape[1])
        Bs[ys[ok], xs[ok]] = True
        vis[Bs & ~A] = (30, 120, 230)
        vis[Bs & A] = (40, 40, 40)
        im = Image.fromarray(vis)
        ImageDraw.Draw(im).rectangle((x0, y0, x1, y1), outline=(0, 180, 0), width=3)
        im.save(out_png)
        out['overlay'] = os.path.abspath(out_png)
    return out



def _download(url, out):
    """시험 원본 이미지(웹 공개 평면도)를 받는다. 실패하면 조용히 넘어가고 suite가 SKIP으로 보고한다."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as fp:
            fp.write(data)
    except Exception as e:  # 네트워크 정책·주소 만료
        print(f'download failed: {url}: {e}', file=sys.stderr)



def _wall_args(p):
    p.add_argument('--profile', help='계열 프로필 JSON(경로 또는 profiles/의 이름)')
    p.add_argument('--wall-rgb', action='append', type=lambda v: [float(x) if '.' in x else int(x) for x in v.split(',')],
                   help='벽 색 r,g,b (0~255 정수 또는 0~1 실수). 여러 번 줄 수 있다')
    p.add_argument('--wall-tol', type=int, help='채널별 허용 차이(0~255, 기본 24)')
    p.add_argument('--dark-sum', type=int, help=f'RGB 합이 이보다 작으면 벽(기본 {DARK_SUM})')



def _spec_from(a, default_none=False):
    if default_none and not (a.profile or a.wall_rgb or a.wall_tol is not None or a.dark_sum is not None):
        return None
    return wall_spec(a.profile, a.wall_rgb, a.wall_tol, a.dark_sum)



def _dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))



def fit_alignment(pairs):
    """Fit a = s*b + d to {x: [[a,b], ...], y: [[a,b], ...]} in pixels.

    Each scalar pair has equal weight. The scale is shared; each axis has its
    own intercept. Both axes need a pair, and at least one must span b values.
    """
    axes = {}
    numerator = denominator = 0.0
    for axis in ('x', 'y'):
        values = np.asarray(pairs.get(axis, []), dtype=float)
        if values.ndim != 2 or values.shape[1] != 2 or not len(values) or not np.isfinite(values).all():
            raise ValueError(f"{axis}: expected nonempty finite [a, b] pairs")
        a, b = values[:, 0], values[:, 1]
        ac, bc = a - a.mean(), b - b.mean()
        numerator += float(np.dot(ac, bc))
        denominator += float(np.dot(bc, bc))
        axes[axis] = (a, b)
    if denominator == 0.0:
        raise ValueError('scale is undetermined: b values must vary within an axis')
    s = numerator / denominator
    offsets, residuals = {}, {}
    all_residuals = []
    for axis, (a, b) in axes.items():
        d = float(a.mean() - s * b.mean())
        r = a - (s * b + d)
        offsets[axis] = d
        residuals[axis] = [{'a': float(av), 'b': float(bv), 'fitted_a': float(s * bv + d),
                            'residual_px': float(rv)} for av, bv, rv in zip(a, b, r)]
        all_residuals.extend(r)
    r = np.asarray(all_residuals)
    return {'scale': s, 'dx_px': offsets['x'], 'dy_px': offsets['y'], 'pairs': residuals,
            'max_residual_px': float(np.max(np.abs(r))), 'rms_residual_px': float(np.sqrt(np.mean(r * r)))}


def run_suite(tests_dir=None, out_dir='suite_out', render_missing=True):
    """Compare source rendering settings and scale evidence with family expectations.

    Missing images are prepared from local PDFs or public URLs when enabled.
    Unavailable source material is reported as skipped, not a successful case.
    """
    tests_dir = tests_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests')
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for name in sorted(os.listdir(tests_dir)):
        src_path = os.path.join(tests_dir, name, 'source.json')
        if not os.path.isfile(src_path):
            continue
        base = os.path.dirname(src_path)
        with open(src_path, encoding='utf-8') as fp:
            src = json.load(fp)
        row = {'family': src.get('family', name) if src.get('family', name) == name else f"{src['family']}/{name}"}
        img = _resolve(src['image'], base)
        r = src.get('render')
        if not os.path.exists(img) and render_missing and r:
            pdf = _resolve(src.get('local_source', ''), base)
            if pdf and os.path.exists(pdf):
                os.makedirs(os.path.dirname(img), exist_ok=True)
                render_pdf(pdf, img, r['page'], r['dpi'], r.get('clip'), r.get('drop_text'))
        if not os.path.exists(img) and render_missing and src.get('url'):
            _download(src['url'], img)
        if not os.path.exists(img):
            row.update(status='skipped', note=f"이미지 없음: {src['image']} (drive_path·url에서 받아 둔다)")
            rows.append(row)
            continue
        with Image.open(img) as im:
            size = list(im.size)
        meta = render_meta(img) if r else None
        render_ok = not r or (meta is not None and meta['page'] == r['page'] and meta['dpi'] == r['dpi']
                             and meta['size_px'] == size and meta.get('drop_text') == r.get('drop_text')
                             and (not r.get('clip') or meta['clip_pt'] == [round(v, 3) for v in r['clip']]))
        row.update(render_passed=bool(render_ok), size_px=size)
        if not render_ok:
            row['render_note'] = '렌더링 사이드카 없음 또는 source.json 설정·이미지 크기와 불일치'
        exp = src.get('expect', {})
        if src.get('scale'):
            with open(os.path.join(base, src['scale']), encoding='utf-8') as fp:
                ev = json.load(fp)
            sc = scale_estimate(ev, base)
            row.update(scale_passed=sc['passed'], m_per_px=sc['m_per_px'], kinds=sc['kinds'],
                       outliers=[sc['items'][i]['label'] for i in sc['outliers']], max_dev=sc['max_abs_deviation'])
            if sc.get('anisotropy_warning'):
                row['x_over_y'] = sc['axes']['x_over_y']
        ok = render_ok and ('scale_passed' not in row or row['scale_passed'] == exp.get('scale_passed', True))
        row['status'] = 'ok' if ok else 'UNEXPECTED'
        rows.append(row)
    with open(os.path.join(out_dir, 'suite.json'), 'w', encoding='utf-8') as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=2)
    return rows


def selftest(out_dir):
    """합성 이미지·PDF로 측정과 자료 준비 기능을 시험한다."""
    os.makedirs(out_dir, exist_ok=True)
    results = []

    def ok(name, cond, detail=''):
        results.append({'test': name, 'passed': bool(cond), 'detail': detail})

    W, H = 420, 320
    bg, wall = (245, 241, 238), (118, 98, 82)  # 원앤온리식 크림 바탕 + 갈색 벽
    im = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, 380, 48), fill=wall)    # 위 외벽 y 40..48
    d.rectangle((40, 272, 380, 280), fill=wall)  # 아래 외벽
    d.rectangle((40, 40, 48, 280), fill=wall)    # 왼쪽 외벽
    d.rectangle((372, 40, 380, 280), fill=wall)  # 오른쪽 외벽
    d.rectangle((200, 48, 205, 140), fill=wall)  # 내벽 x 200..205 (문 140..176)
    d.rectangle((200, 176, 205, 272), fill=wall)
    d.line((60, 200, 150, 200), fill=(150, 140, 130), width=1)  # 가구 선(무시돼야 함)
    d.rectangle((60, 296, 160, 300), fill=wall)  # 축척 막대 100px = 10ft
    plan = os.path.join(out_dir, 'synthetic_plan.png')
    im.save(plan)
    prof = {'family': 'synthetic', 'background_rgb': list(bg), 'wall': {'rgb': [list(wall)], 'tol': 24}}
    with open(os.path.join(out_dir, 'synthetic_profile.json'), 'w') as fp:
        json.dump(prof, fp)
    sv = survey(plan)
    got = sv.get('wall', {}).get('rgb', [[0, 0, 0]])[0]
    ok('survey: 바탕·벽 색 추정', max(abs(a - b) for a, b in zip(sv['background_rgb'], bg)) <= 8 and
       max(abs(a - b) for a, b in zip(got, wall)) <= 12, {'background': sv['background_rgb'], 'wall': got})

    ev = {'image': 'synthetic_plan.png', 'profile': prof, 'items': [
        {'kind': 'bar_box', 'box': [55, 290, 170, 306], 'length': "10'"},
        {'kind': 'dim', 'p0': [48, 160], 'p1': [200, 160], 'length': "15'-0\"", 'label': '서쪽 방 폭'},
        {'kind': 'area', 'polygon': [[48, 48], [372, 48], [372, 272], [48, 272]], 'area': f'{324 * 224 * 0.03048 ** 2:.2f} m2'}]}
    sc = scale_estimate(ev, out_dir)
    ok('scale: 막대·치수·면적 대조 통과', sc['passed'] and abs(sc['m_per_px'] / 0.03048 - 1) < 0.01, sc['m_per_px'])
    ev['items'].append({'kind': 'dim', 'p0': [48, 160], 'p1': [200, 160], 'length': "18'", 'label': '틀린 치수'})
    sc2 = scale_estimate(ev, out_dir)
    ok('scale: 틀린 근거를 이상치로 보고', 3 in sc2['outliers'], sc2['outliers'])
    iso = {'items': [{'kind': 'dim', 'p0': [0, 0], 'p1': [200, 0], 'length': '4000 mm'}, {'kind': 'dim', 'p0': [0, 0], 'p1': [150, 0], 'length': '3000 mm'},
                     {'kind': 'dim', 'p0': [0, 0], 'p1': [0, 250], 'length': '5000 mm'}, {'kind': 'dim', 'p0': [0, 0], 'p1': [0, 100], 'length': '2000 mm'}]}
    stretched = {'items': [dict(it, p1=[it['p1'][0] / 1.12, it['p1'][1]]) for it in iso['items']]}  # 가로만 12% 줄인 그림
    s_iso, s_st = scale_estimate(iso), scale_estimate(stretched)
    ok('scale: 가로·세로 축척 비교(비등방 경고)', 'anisotropy_warning' not in s_iso and 'anisotropy_warning' in s_st and
       abs(s_st['axes']['x_over_y'] - 1.12) < 0.01, s_st.get('axes'))
    ok('scale: 짧은 길이는 픽셀 양자화만큼 허용', scale_estimate({'items': [{'kind': 'dim', 'p0': [0, 0], 'p1': [26, 0], 'length': '500 mm'}]})['items'][0]['tol'] > 0.07)
    ok('parse_length', abs(parse_length("12'-6\"") - 3.81) < 1e-3 and abs(parse_length('3810 mm') - 3.81) < 1e-9)

    shifted = Image.new('RGB', (W + 40, H + 40), bg)
    shifted.paste(im, (13, 27))
    sp = os.path.join(out_dir, 'synthetic_shifted.png')
    shifted.save(sp)
    al = align_floors(plan, sp, (190, 40, 260, 160), 40, wall_spec(profile=prof), os.path.join(out_dir, 'synthetic_align.png'))
    ok('align: 이동 (13, 27) 복원', (al['dx_px'], al['dy_px']) == (13, 27) and al['core_iou'] > 0.95, al)
    try:
        fitz = _fitz()
    except SystemExit:
        fitz = None
    if fitz:
        doc = fitz.open()
        pg = doc.new_page(width=300, height=200)
        grey = (0.71, 0.71, 0.71)
        sh = pg.new_shape()
        sh.draw_rect(fitz.Rect(20, 20, 220, 120))  # 회색 덩어리(벽 색이지만 뒤에 덮임)
        sh.finish(fill=grey, color=None)
        sh.draw_rect(fitz.Rect(20, 20, 120, 120))  # 왼쪽 절반을 흰색이 덮는다
        sh.finish(fill=(1, 1, 1), color=None)
        for i in range(4):  # 칸 4개짜리 축척 막대 4 × 18pt = 72pt
            sh.draw_rect(fitz.Rect(40 + 18 * i, 160, 58 + 18 * i, 164))
            sh.finish(fill=(0, 0, 0) if i % 2 == 0 else (1, 1, 1), color=(0, 0, 0), width=0.3)
        sh.commit()
        pg.insert_text((40, 175), '0', fontsize=6)
        pg.insert_text((108, 175), "10'", fontsize=6)
        pdfp = os.path.join(out_dir, 'synthetic.pdf')
        doc.save(pdfp)
        doc2 = fitz.open(pdfp)
        hr = pdf_hidden_ratio(doc2[0], grey, 144)
        ok('pdf: 덮인 비율(절반)', hr['hidden'] is not None and abs(hr['hidden'] - 0.5) < 0.05, hr)
        bars = pdf_scale_bars(doc2[0])
        ok('pdf: 축척 막대 72pt와 라벨', bars and abs(bars[0]['length_pt'] - 72) < 0.6 and "10'" in bars[0]['labels'],
           bars[:1])
        meta = render_pdf(pdfp, os.path.join(out_dir, 'synthetic_render.png'), 1, 144)
        x, y = pt_to_px(meta, 40, 160)
        ok('render: 사이드카 pt→px', abs(x - 80) < 1e-6 and abs(y - 320) < 1e-6 and meta['size_px'] == [600, 400], meta['size_px'])
    m = wall_mask(np.asarray(im), wall_spec(profile=prof))
    ok('runs: profile row and column', runs_row(m, 44, 0, W) == [(40, 380)] and
       runs_col(m, 202, 0, H) == [(40, 140), (176, 280)])
    points = [(20 + 80 * math.cos(t), 30 + 80 * math.sin(t)) for t in np.linspace(0, 2, 20)]
    cx, cy, radius, worst, std = fit_circle(points)
    ok('arc: circle recovery', abs(cx - 20) < 1e-8 and abs(cy - 30) < 1e-8 and
       abs(radius - 80) < 1e-8 and worst < 1e-8)
    pairs = {axis: [[1.0035 * b + d + noise, b] for b, noise in zip((0, 1000, 2000, 3000, 4000),
              (0.3, -0.3, 0.0, -0.3, 0.3))] for axis, d in (('x', 81.1), ('y', 19.6))}
    fit = fit_alignment(pairs)
    ok('fitalign: 1.0035, +81.1/+19.6 with ±0.3px noise', abs(fit['scale'] - 1.0035) < 1e-12 and
       abs(fit['dx_px'] - 81.1) < 1e-9 and abs(fit['dy_px'] - 19.6) < 1e-9 and
       abs(fit['max_residual_px'] - 0.3) < 1e-9 and abs(fit['rms_residual_px'] - math.sqrt(0.072)) < 1e-9, fit)
    # A single pair on one axis still identifies that intercept using the other axis's scale.
    fit = fit_alignment({'x': [[5, 1], [9, 3]], 'y': [[17, 4]]})
    ok('fitalign: shared scale with one y pair', abs(fit['scale'] - 2) < 1e-12 and
       abs(fit['dx_px'] - 3) < 1e-12 and abs(fit['dy_px'] - 9) < 1e-12)
    fit = fit_alignment({'x': [[3, 0], [7, 2]], 'y': [[9, 0], [21, 4]]})
    ok('fitalign: one least-squares scale for unequal axis spans', abs(fit['scale'] - 2.8) < 1e-12 and
       abs(fit['dx_px'] - 2.2) < 1e-12 and abs(fit['dy_px'] - 9.4) < 1e-12 and
       abs(fit['pairs']['x'][0]['residual_px'] - 0.8) < 1e-12 and
       abs(fit['pairs']['y'][0]['residual_px'] + 0.4) < 1e-12)
    for bad in ({'x': [[1, 2]], 'y': [[3, 4]]}, {'x': [], 'y': [[3, 4]]},
                {'x': [[float('nan'), 2]], 'y': [[3, 4]]}):
        try:
            fit_alignment(bad)
        except ValueError:
            ok('fitalign: rejects undetermined or invalid pairs', True)
        else:
            ok('fitalign: rejects undetermined or invalid pairs', False)
    passed = all(r['passed'] for r in results)
    with open(os.path.join(out_dir, 'selftest.json'), 'w', encoding='utf-8') as fp:
        json.dump({'passed': passed, 'results': results}, fp, ensure_ascii=False, indent=2, default=str)
    return passed, results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('survey', help='계열 프로필 초안')
    p.add_argument('plan')
    p.add_argument('--pages', type=lambda v: [int(x) for x in v.split(',')], help='PDF 페이지(1부터), 예: 1,3')
    p.add_argument('--dpi', type=float)
    p.add_argument('--out')
    p = sub.add_parser('render', help='PDF 페이지 → PNG(+pt 변환 사이드카)')
    p.add_argument('pdf')
    p.add_argument('out')
    p.add_argument('--page', type=int, default=1)
    p.add_argument('--dpi', type=float, default=300)
    p.add_argument('--clip', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'), help='pt 단위')
    p.add_argument('--drop-text', help="이 정규식에 맞는 텍스트 span(워터마크 등)을 지우고 렌더링. 예: 'www\\.'")
    p = sub.add_parser('pdfbars', help='PDF 페이지의 축척 막대 후보(pt)')
    p.add_argument('pdf')
    p.add_argument('--page', type=int, default=1)
    p = sub.add_parser('pdfdims', help='PDF 페이지 글자층의 방 치수(위치 pt 포함)')
    p.add_argument('pdf')
    p.add_argument('--page', type=int, default=1)
    p = sub.add_parser('tiles')
    p.add_argument('plan')
    p.add_argument('out_dir')
    p.add_argument('--tile', type=int, default=260)
    p.add_argument('--scale', type=int, default=3)
    p.add_argument('--step', type=int, default=10)
    p = sub.add_parser('crop')
    p.add_argument('plan')
    p.add_argument('out')
    p.add_argument('box', type=int, nargs=4)
    p.add_argument('--scale', type=int, default=3)
    p.add_argument('--step', type=int, default=10)
    p = sub.add_parser('runs')
    p.add_argument('plan')
    p.add_argument('axis', choices=('row', 'col'))
    p.add_argument('index', type=int)
    p.add_argument('lo', type=int)
    p.add_argument('hi', type=int)
    p.add_argument('--band', type=int, default=0)
    p.add_argument('--min-len', type=int, default=1)
    _wall_args(p)
    p = sub.add_parser('arc')
    p.add_argument('plan')
    p.add_argument('lo', type=int)
    p.add_argument('hi', type=int)
    p.add_argument('--side', default='top', choices=('top', 'bottom', 'left', 'right'))
    p.add_argument('--step', type=int, default=2)
    p.add_argument('--profile', help='background_rgb가 있으면 그 색이 아닌 첫 픽셀을 외곽으로 본다')
    p = sub.add_parser('scale', help='축척 근거 대조')
    p.add_argument('evidence')
    p.add_argument('--tol', type=float, help='모든 근거에 같은 허용 오차(비율). 기본은 종류별')
    p = sub.add_parser('align', help='층 위치 맞춤(코어 기준)')
    p.add_argument('base')
    p.add_argument('other')
    p.add_argument('--core', type=int, nargs=4, required=True)
    p.add_argument('--search', type=int, default=150)
    p.add_argument('--out')
    _wall_args(p)
    p = sub.add_parser('suite', help='계열 시험 묶음(tests/*/source.json) 실행')
    p.add_argument('--tests')
    p.add_argument('--out', default=os.path.join(os.getcwd(), 'plan_tools_suite'))
    p.add_argument('--no-render', action='store_true')
    p = sub.add_parser('selftest', help='자료 없이 도구 자체 시험')
    p.add_argument('--out', default=os.path.join(os.getcwd(), 'plan_tools_selftest'))
    p = sub.add_parser('fitalign', help='측정 쌍으로 공통 배율·축별 이동 최소제곱 맞춤')
    p.add_argument('pairs')
    p.add_argument('--out')
    a = ap.parse_args(argv)

    if a.cmd == 'survey':
        prof = survey(a.plan, a.pages, a.dpi)
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            with open(a.out, 'w', encoding='utf-8') as fp:
                json.dump(prof, fp, ensure_ascii=False, indent=2, default=str)
        _dump({k: prof[k] for k in ('format', 'background_rgb', 'wall', 'units', 'scale_evidence_found', 'quirks')
               if k in prof})
    elif a.cmd == 'render':
        _dump(render_pdf(a.pdf, a.out, a.page, a.dpi, a.clip, a.drop_text))
    elif a.cmd == 'pdfbars':
        _dump(pdf_scale_bars(_fitz().open(a.pdf)[a.page - 1]))
    elif a.cmd == 'pdfdims':
        _dump(pdf_dims(_fitz().open(a.pdf)[a.page - 1]))
    elif a.cmd == 'tiles':
        for path in tiles(a.plan, a.out_dir, a.tile, scale=a.scale, step=a.step):
            print(path)
    elif a.cmd == 'crop':
        print(grid_crop(a.plan, a.out, a.box, a.scale, a.step))
    elif a.cmd == 'runs':
        _, arr = load_rgb(a.plan)
        m = wall_mask(arr, _spec_from(a))
        fn = runs_row if a.axis == 'row' else runs_col
        print(json.dumps(fn(m, a.index, a.lo, a.hi, a.band, a.min_len)))
    elif a.cmd == 'arc':
        im, arr = load_rgb(a.plan)
        rgb = arr.astype(np.int32).sum(2)
        mask = ~background_mask(arr, a.profile) if a.profile else None
        pts = edge_points(rgb, a.lo, a.hi, a.side, a.step, mask=mask)
        cx, cy, r, worst, std = fit_circle(pts)
        print(json.dumps({'center': [round(cx, 2), round(cy, 2)], 'radius': round(r, 2),
                          'max_residual_px': round(worst, 2), 'residual_std_px': round(std, 2), 'points': len(pts)}))
    elif a.cmd == 'scale':
        with open(a.evidence, encoding='utf-8') as fp:
            ev = json.load(fp)
        rep = scale_estimate(ev, os.path.dirname(os.path.abspath(a.evidence)), a.tol)
        _dump(rep)
        return 0 if rep['passed'] else 1
    elif a.cmd == 'align':
        _dump(align_floors(a.base, a.other, a.core, a.search, _spec_from(a), a.out))
    elif a.cmd == 'fitalign':
        with open(a.pairs, encoding='utf-8') as fp:
            pairs = json.load(fp)
        try:
            rep = fit_alignment(pairs)
        except ValueError as exc:
            ap.error(str(exc))
        if a.out:
            with open(a.out, 'w', encoding='utf-8') as fp:
                json.dump(rep, fp, ensure_ascii=False, indent=2)
        _dump(rep)
    elif a.cmd == 'suite':
        rows = run_suite(a.tests, a.out, not a.no_render)
        for r in rows:
            if r['status'] == 'skipped':
                print(f"SKIP {r['family']}: {r['note']}")
                continue
            print(f"{'OK  ' if r['status'] == 'ok' else 'FAIL'} {r['family']}: render {'PASS' if r['render_passed'] else 'FAIL'}"
                  f" | scale {r.get('m_per_px')} m/px {r.get('kinds')} {'PASS' if r.get('scale_passed') else 'FAIL'}"
                  f"{' 이상치 ' + str(len(r['outliers'])) if r.get('outliers') else ''}"
                  f"{' 비등방 x/y ' + str(r['x_over_y']) if r.get('x_over_y') else ''}")
        bad = [r for r in rows if r['status'] == 'UNEXPECTED']
        print('suite:', 'PASS' if not bad else 'FAIL', os.path.join(a.out, 'suite.json'))
        return 1 if bad else 0
    elif a.cmd == 'selftest':
        passed, results = selftest(a.out)
        for r in results:
            print(('PASS ' if r['passed'] else 'FAIL ') + r['test'] + ('' if r['passed'] else f"  {r['detail']}"))
        print('selftest:', 'PASS' if passed else 'FAIL', os.path.join(a.out, 'selftest.json'))
        return 0 if passed else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
