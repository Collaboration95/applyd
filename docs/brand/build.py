"""Original, manually plotted rect artwork. Rebuild only this directory."""
from pathlib import Path
import base64
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
PAL = dict(ink='#242638', shadow='#765444', coat='#C18D55', gold='#EDBE77', skin='#FFD9AE', paper='#FFF4D9', teal='#3C9D98', lens='#9CE0D4', blush='#DD806C')

def rect(x,y,w,h,c):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{PAL[c]}"/>'

def group(items, tx=0, ty=0, scale=1):
    return f'<g transform="translate({tx} {ty}) scale({scale})">'+''.join(items)+'</g>'

def mascot(frame=0):
    s=[]
    def r(x,y,w,h,c): s.append(rect(x,y,w,h,c))
    # Feet and a compact, double-breasted trench coat.
    if frame == 1:
        r(9,27,4,2,'ink'); r(8,29,5,1,'ink'); r(18,27,4,3,'ink'); r(19,30,4,1,'ink')
    else:
        r(10,27,4,3,'ink'); r(9,29,5,1,'ink'); r(18,27,4,3,'ink'); r(18,29,5,1,'ink')
    body=[]
    # The carried frame bobs one grid pixel without moving the foot baseline.
    bob=-1 if frame==1 else 0
    def b(x,y,w,h,c): body.append(rect(x,y,w,h,c))
    b(10,17,11,2,'ink'); b(9,19,13,2,'ink'); b(8,21,15,3,'ink'); b(9,24,13,4,'ink')
    b(10,19,11,8,'coat'); b(9,21,2,3,'gold'); b(20,20,2,5,'shadow'); b(10,26,11,1,'gold')
    b(14,18,3,4,'paper'); b(15,19,1,3,'teal')
    b(11,18,3,2,'gold'); b(12,20,2,1,'gold'); b(13,21,1,1,'gold')
    b(17,18,3,2,'gold'); b(17,20,2,1,'gold'); b(17,21,1,1,'gold')
    b(15,22,1,5,'shadow'); b(17,23,1,1,'ink'); b(17,25,1,1,'ink')
    # Large head, tiny ears, open expression.
    b(8,12,2,3,'ink'); b(22,12,1,3,'ink'); b(9,10,13,6,'ink'); b(10,16,11,2,'ink')
    b(9,12,1,2,'skin'); b(10,11,11,5,'skin'); b(11,16,9,1,'skin'); b(20,11,1,5,'gold')
    if frame==3:
        b(11,12,2,1,'ink'); b(12,13,1,1,'ink'); b(18,12,2,1,'ink'); b(18,13,1,1,'ink')
    else:
        b(12,12,1,2,'ink'); b(18,12,1,2,'ink')
    b(10,14,2,1,'blush'); b(19,14,1,1,'blush'); b(14,15,3,1,'ink')
    # Fedora: stepped crown, teal band, generous brim.
    b(11,2,8,1,'ink'); b(10,3,10,1,'ink'); b(9,4,12,4,'ink')
    b(11,3,8,1,'gold'); b(10,4,10,3,'coat'); b(11,4,3,1,'gold'); b(19,4,1,3,'shadow')
    b(10,7,10,1,'teal'); b(18,7,2,1,'lens')
    b(7,8,16,1,'ink'); b(6,9,18,1,'ink'); b(8,10,14,1,'ink')
    b(8,8,14,1,'gold'); b(7,9,16,1,'coat'); b(19,9,4,1,'shadow')
    # Glass: one-pixel outline, mint glass, block reflection and angled handle.
    b(7,20,3,2,'ink'); b(8,22,3,2,'ink'); b(10,24,2,1,'ink')
    b(8,20,1,2,'shadow'); b(9,22,1,1,'coat')
    b(3,13,5,1,'ink'); b(2,14,7,1,'ink'); b(1,15,9,4,'ink'); b(2,19,7,1,'ink'); b(3,20,5,1,'ink')
    b(3,14,5,1,'teal'); b(2,15,7,4,'teal'); b(3,19,5,1,'teal')
    b(3,15,5,4,'lens'); b(3,15,2,1,'paper'); b(3,16,1,1,'paper')
    b(9,22,2,2,'skin'); b(10,22,1,1,'paper')
    if frame != 2:
        # Neat staggered CV stack, held under the right arm.
        b(24,17,5,2,'ink'); b(23,18,7,2,'ink'); b(24,18,4,1,'paper')
        b(22,19,8,8,'ink'); b(23,20,6,6,'paper'); b(28,20,1,1,'gold')
        b(24,21,3,1,'teal'); b(24,23,4,1,'shadow'); b(24,24,3,1,'gold')
        b(21,23,3,3,'ink'); b(22,23,2,2,'skin'); b(22,23,1,1,'paper')
    else:
        # Submitting: right hand presses a stamp while a checked page flies up.
        b(21,20,3,2,'ink'); b(21,21,4,2,'coat'); b(23,22,3,2,'ink'); b(23,22,2,1,'skin')
        b(23,24,3,2,'ink'); b(24,24,1,2,'teal'); b(22,26,5,1,'ink')
        b(21,27,8,2,'ink'); b(22,27,6,1,'teal'); b(24,29,6,1,'paper')
        b(25,8,5,1,'ink'); b(24,9,7,6,'ink'); b(25,9,5,5,'paper')
        b(25,10,3,1,'gold'); b(26,12,1,1,'teal'); b(27,13,1,1,'teal'); b(28,11,1,2,'teal')
        b(26,17,3,1,'teal'); b(28,16,2,1,'teal'); b(29,19,1,1,'gold')
    if frame==3:
        # Four-point found-it sparkle, with its own dark pixel contour.
        b(26,6,1,2,'ink'); b(25,8,3,1,'ink'); b(23,9,7,1,'ink'); b(25,10,3,1,'ink'); b(26,11,1,2,'ink')
        b(26,8,1,3,'gold'); b(25,9,3,1,'gold'); b(26,9,1,1,'paper')
        b(29,5,1,1,'teal'); b(23,5,1,1,'gold')
    s.append(group(body,ty=bob))
    return ''.join(s)

def flatten_rects(body):
    tree=ET.fromstring('<g>'+body+'</g>')
    def visit(node, tx=0, ty=0, scale=1):
        transform=node.get('transform','')
        match=re.fullmatch(r'translate\(([-.0-9]+) ([-.0-9]+)\) scale\(([-.0-9]+)\)', transform)
        if match:
            x,y,k=map(float,match.groups())
            tx,ty,scale=tx+x*scale,ty+y*scale,scale*k
        if node.tag=='rect':
            node.set('x',f"{tx+float(node.get('x'))*scale:g}")
            node.set('y',f"{ty+float(node.get('y'))*scale:g}")
            node.set('width',f"{float(node.get('width'))*scale:g}")
            node.set('height',f"{float(node.get('height'))*scale:g}")
            return ET.tostring(node,encoding='unicode')
        if node.tag=='text': return ET.tostring(node,encoding='unicode')
        return ''.join(visit(ch,tx,ty,scale) for ch in node)
    return visit(tree)

def svg(w,h,body,title,desc):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" role="img" aria-labelledby="title desc"><title id="title">{title}</title><desc id="desc">{desc}</desc>{body}</svg>'''

GLYPHS={
'a':['00000','00000','01110','00001','01111','10001','01111','00000','00000'],
'p':['00000','00000','11110','10001','10001','11110','10000','10000','10000'],
'l':['01100','00100','00100','00100','00100','00100','00011','00000','00000'],
'y':['00000','00000','10001','10001','10001','01111','00001','10001','01110'],
'd':['00001','00001','01111','10001','10001','10001','01111','00000','00000']}

def wordmark(x,y,scale):
    pixels,cursor=[],0
    for ch in 'applyd':
        cols=[c for c in range(5) if any(row[c]=='1' for row in GLYPHS[ch])]
        for py,row in enumerate(GLYPHS[ch]):
            for px in cols:
                if row[px]=='1': pixels.append(rect(cursor+px-cols[0],py,1,1,'ink'))
        cursor+=cols[-1]-cols[0]+2
    return group(pixels,x,y,scale)

(ROOT/'sprite.svg').write_text(svg(32,32,mascot(),'applyd detective mascot','A small smiling detective in a tan trench coat and teal-banded hat holds a magnifying glass and a neat CV stack. Original nine-colour pixel art.'))
frames=['Idle — found a job','Walking — carrying CVs','Submitting — stamping and sending','Done — found-it sparkle']
(ROOT/'spritesheet.svg').write_text(svg(128,32,''.join(f'<g transform="translate({i*32} 0)" data-frame="{i}" aria-label="{frames[i]}">{mascot(i)}</g>' for i in range(4)),'applyd four-frame sprite sheet','Four equal 32 by 32 frames, left to right: idle, carrying, submitting with a flying page, done with a sparkle.'))
background=rect(0,0,1280,320,'paper')
banner=background+group([mascot()],64,32,8)+wordmark(392,56,16)+f'<text x="392" y="256" font-family="Helvetica, Arial, sans-serif" font-size="28" fill="{PAL["shadow"]}" shape-rendering="geometricPrecision">Local-first job application agent</text>'
(ROOT/'banner.svg').write_text(svg(1280,320,flatten_rects(banner),'applyd — Local-first job application agent','Neutral cream README banner with a crisp enlarged detective mascot, original pixel wordmark and functional subtitle.'))
social=rect(0,0,1280,640,'paper')+group([mascot()],64,96,14)+wordmark(608,192,16)+f'<text x="608" y="408" font-family="Helvetica, Arial, sans-serif" font-size="30" fill="{PAL["shadow"]}" shape-rendering="geometricPrecision"><tspan x="608">Local-first job</tspan><tspan x="608" dy="42">application agent</tspan></text>'
(ROOT/'social-preview.svg').write_text(svg(1280,640,flatten_rects(social),'applyd social preview','Detective mascot and pixel wordmark on an opaque cream background. Local-first job application agent.'))

magick='/opt/homebrew/bin/magick'
for size in [64,128,256]:
    subprocess.run([magick,'-background','none',str(ROOT/'sprite.svg'),'-filter','point','-sample',f'{size}x{size}',str(ROOT/f'sprite-{size}.png')],check=True)
subprocess.run([magick,'-background','none',str(ROOT/'spritesheet.svg'),'-filter','point','-sample','512x128',str(ROOT/'spritesheet-512x128.png')],check=True)
for name in ['banner','social-preview']:
    subprocess.run([magick,'-background','none','-font','/System/Library/Fonts/Helvetica.ttc',str(ROOT/f'{name}.svg'),'-filter','point',str(ROOT/f'{name}.png')],check=True)

# Data URIs make the preview portable as a single offline HTML file.
def uri(name):
    mime='image/svg+xml' if name.endswith('.svg') else 'image/png'
    return f'data:{mime};base64,'+base64.b64encode((ROOT/name).read_bytes()).decode()

def panel(theme):
    sizes=''.join(f'<figure><img class="pixel" width="{n}" height="{n}" src="{uri("sprite.svg")}" alt="Detective mascot at {n} pixels"><figcaption>{n} px</figcaption></figure>' for n in [32,64,128])
    exports=''.join(f'<figure><img class="pixel" width="{min(n,128)}" src="{uri(f"sprite-{n}.png")}" alt="{n} by {n} PNG export"><figcaption>sprite-{n}.png</figcaption></figure>' for n in [64,128,256])
    return f'''<section class="panel {theme}" aria-label="{theme} background preview"><h2>{theme.title()} background</h2><div class="sizes">{sizes}<figure><div class="animated" role="img" aria-label="Animated detective: idle, carry, submit, done"></div><figcaption>4-frame loop</figcaption></figure></div><h3>Sprite sheet · idle / carry / submit / done</h3><img class="sheet pixel" src="{uri('spritesheet-512x128.png')}" alt="All four frames at 4 times native size"><h3>PNG exports</h3><div class="exports">{exports}</div><h3>README banner · SVG</h3><img class="wide" src="{uri('banner.svg')}" alt="Vector README banner"><h3>README banner · PNG</h3><img class="wide" src="{uri('banner.png')}" alt="PNG README banner"><h3>Social preview · 1280 × 640</h3><img class="wide" src="{uri('social-preview.png')}" alt="Opaque social preview"></section>'''
css=f''':root{{color-scheme:light dark}}*{{box-sizing:border-box}}html,body{{margin:0;background:transparent;font-family:system-ui,sans-serif}}main{{padding:0}}header{{color:var(--foreground,#242638);margin-bottom:20px}}h1{{font-size:22px;margin:0 0 6px}}header p{{margin:0;font-size:14px}}.panels{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}}.panel{{min-width:0;padding:24px;border-radius:12px}}.light{{background:#f6f8fa;color:#242638}}.dark{{background:#0d1117;color:#f0f3f6}}h2{{font-size:16px;margin:0 0 24px}}h3{{font-size:12px;font-weight:600;margin:24px 0 12px}}figure{{margin:0;text-align:center}}figcaption{{font-size:11px;opacity:.75;margin-top:10px}}.sizes,.exports{{display:flex;gap:18px;align-items:center;flex-wrap:wrap;min-height:168px}}.pixel{{image-rendering:pixelated}}.sheet,.wide{{display:block;max-width:100%;height:auto}}.sheet{{width:512px}}.wide{{width:100%}}.animated{{width:96px;height:96px;background-image:url('{uri('spritesheet.svg')}');background-repeat:no-repeat;background-size:384px 96px;image-rendering:pixelated;animation:work 2.4s steps(4,end) infinite}}@keyframes work{{from{{background-position:0 0}}to{{background-position:-384px 0}}}}@media(prefers-reduced-motion:reduce){{.animated{{animation:none}}}}@media(max-width:560px){{.panel{{padding:16px}}.panels{{gap:8px}}.sizes,.exports{{gap:12px}}.sizes img,.exports img{{max-width:100%;height:auto}}}}'''
html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>applyd detective artwork preview</title><style>'+css+'</style></head><body><main><header><h1>applyd · detective</h1><p>Original 32 × 32 pixel mascot · 9 colours · 4 frames</p></header><div class="panels">'+panel('light')+panel('dark')+'</div></main></body></html>'
(ROOT/'preview.html').write_text(html)
(ROOT/'NOTES.md').write_text('Palette: ink #242638, shadow #765444, coat #C18D55, gold #EDBE77, skin #FFD9AE, paper #FFF4D9, teal #3C9D98, lens #9CE0D4, blush #DD806C.\nFrames (32×32 each, left to right): idle; walking/carrying CVs; stamping/submitting with a flying checked page; done with a sparkle.\nOriginal chibi detective: an oversized fedora, clear face, magnifying glass and tidy CV stack communicate a friendly agent doing the paperwork.\nDark contours define the silhouette; warm fills and mint glass remain readable on light and dark surfaces. Banners use an opaque neutral cream for consistent contrast.\nAll mascot shapes and the custom wordmark are manually plotted rectangles; build.py reproduces the assets with ImageMagick point sampling. Preview is self-contained and respects reduced motion.\n')
# Structural checks: no external artwork, only integer rectangle coordinates.
for name in ['sprite.svg','spritesheet.svg']:
    tree=ET.fromstring((ROOT/name).read_text())
    colors={e.get('fill') for e in tree.iter() if e.tag.endswith('rect')}
    assert colors <= set(PAL.values()) and len(colors)==9
    for e in tree.iter():
        if e.tag.endswith('rect'):
            for k in ['x','y','width','height']: assert float(e.get(k)).is_integer()
for p in sorted(ROOT.iterdir()): print(p.name, p.stat().st_size)
