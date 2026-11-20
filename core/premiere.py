"""Export XML (FCP7 xmeml) per Premiere Pro: una sequenza 1080x1920 per clip.
V1 = gioco (sorgente originale, ritaglio + scala + posizione)  -> modificabile
V2 = cam   (sorgente originale, ritaglio + scala + posizione)  -> modificabile
V3 = clip verticale già renderizzata (disattivata, come riferimento/backup)
A1/A2 = audio originale
"""
import os
from urllib.parse import quote
from xml.sax.saxutils import escape
from .render import heights, clamp_rect, OUT_W, OUT_H


def path_url(p):
    p = os.path.abspath(p).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://localhost" + quote(p, safe="/:")


def build_xml(clips, src, info, layout, rendered, out_xml):
    fps = info["fps"]
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - tb) > 0.01 else "FALSE"
    F = lambda s: int(round(s * fps))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    sw, sh = info["w"], info["h"]
    src_frames = F(info["dur"])
    src_name = os.path.basename(src)
    cam_h, game_h = heights(layout)
    defined = set()

    def file_el(fid, path, name, frames, w, h, audio=True):
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        aud = (f"<audio><samplecharacteristics><depth>16</depth><samplerate>{info['sr']}</samplerate>"
               f"</samplecharacteristics><channelcount>{info['ch']}</channelcount></audio>") if audio else ""
        return (f'<file id="{fid}"><name>{escape(name)}</name><pathurl>{escape(path_url(path))}</pathurl>{rate}'
                f"<duration>{frames}</duration><timecode>{rate}<string>00:00:00:00</string><frame>0</frame>"
                f"<displayformat>NDF</displayformat></timecode><media><video><samplecharacteristics>{rate}"
                f"<width>{w}</width><height>{h}</height><anamorphic>FALSE</anamorphic>"
                f"<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
                f"</samplecharacteristics></video>{aud}</media></file>")

    def param(pid, name, val, lo=0, hi=100):
        return (f"<parameter><parameterid>{pid}</parameterid><name>{name}</name><valuemin>{lo}</valuemin>"
                f"<valuemax>{hi}</valuemax><value>{val}</value></parameter>")

    def layer_filters(r, target_cy):
        r = clamp_rect(r, info)
        scale = OUT_W / r["w"] * 100.0
        s = scale / 100.0
        dx = r["x"] + r["w"] / 2 - sw / 2
        dy = r["y"] + r["h"] / 2 - sh / 2
        ax = OUT_W / 2 - dx * s
        ay = target_cy - dy * s
        horiz = (ax - OUT_W / 2) / OUT_W
        vert = (ay - OUT_H / 2) / OUT_H
        crop = ("<filter><effect><name>Crop</name><effectid>crop</effectid><effectcategory>motion</effectcategory>"
                "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                + param("left", "left", round(r["x"] / sw * 100, 4))
                + param("right", "right", round((sw - r["x"] - r["w"]) / sw * 100, 4))
                + param("top", "top", round(r["y"] / sh * 100, 4))
                + param("bottom", "bottom", round((sh - r["y"] - r["h"]) / sh * 100, 4))
                + "</effect></filter>")
        motion = ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
                  "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                  + param("scale", "Scale", round(scale, 3), 0, 1000)
                  + "<parameter><parameterid>center</parameterid><name>Center</name>"
                  f"<value><horiz>{horiz:.6f}</horiz><vert>{vert:.6f}</vert></value></parameter>"
                  + "</effect></filter>")
        return crop + motion

    x = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE xmeml>", '<xmeml version="4"><bin><name>Jarvis Clipper</name><children>']
    for c in clips:
        a, b = F(c["start"]), F(c["end"])
        d = b - a
        nm = f"Clip {c['id']:02d} - {c['score']} - {c.get('title', '')}"[:80]
        x.append(f"<sequence><name>{escape(nm)}</name>{rate}<duration>{d}</duration>"
                 f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
                 f"<media><video><format><samplecharacteristics>{rate}<width>{OUT_W}</width><height>{OUT_H}</height>"
                 "<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
                 "</samplecharacteristics></format>")

        def vclip(cid, label, filt):
            return (f'<track><clipitem id="{cid}"><name>{escape(label)}</name><enabled>TRUE</enabled>'
                    f"<duration>{src_frames}</duration>{rate}<start>0</start><end>{d}</end><in>{a}</in><out>{b}</out>"
                    + file_el("src", src, src_name, src_frames, sw, sh) + filt + "</clipitem></track>")
        x.append(vclip(f"g{c['id']}", "GIOCO", layer_filters(layout["game"], cam_h + game_h / 2)))
        if cam_h:
            x.append(vclip(f"c{c['id']}", "CAM", layer_filters(layout["cam"], cam_h / 2)))
        rp = rendered.get(c["id"]) if rendered else None
        if rp:
            rf = f"r{c['id']}"
            x.append(f'<track><clipitem id="{rf}i"><name>RIFERIMENTO (render)</name><enabled>FALSE</enabled>'
                     f"<duration>{d}</duration>{rate}<start>0</start><end>{d}</end><in>0</in><out>{d}</out>"
                     + file_el(rf, rp, os.path.basename(rp), d, OUT_W, OUT_H) + "</clipitem></track>")
        x.append("</video><audio>")
        if info.get("has_audio", True):
            for ch in range(1, min(info["ch"], 2) + 1):
                x.append(f'<track><clipitem id="a{c["id"]}_{ch}"><name>{escape(src_name)}</name><enabled>TRUE</enabled>'
                         f"<duration>{src_frames}</duration>{rate}<start>0</start><end>{d}</end><in>{a}</in><out>{b}</out>"
                         + file_el("src", src, src_name, src_frames, sw, sh)
                         + f"<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex></sourcetrack>"
                         "</clipitem></track>")
        x.append("</audio></media></sequence>")
    x.append("</children></bin></xmeml>")
    with open(out_xml, "w", encoding="utf-8") as f:
        f.write("".join(x))
    return out_xml


def build_autoedit_xml(ed, src, info, cam, out_xml, name="Montaggio Jarvis", fx=None, bip=None):
    """Sequenza orizzontale, tutto modificabile:
    V1 = il video tagliato (clip intere, collegate al loro audio);
    V2 = gli zoom come livello sopra (stessa sorgente con Movimento): si spostano, si allungano, si cancellano;
    V3 = entrate/uscite degli zoom già renderizzate con il motion blur;
    A1 = audio originale in un'unica traccia stereo (buchi sulle parolacce), A2 = suoni di censura."""
    from .autoedit import view, subpieces, build_pieces
    fps = info["fps"]
    tb = int(round(fps)); ntsc = "TRUE" if abs(fps - tb) > 0.01 else "FALSE"
    F = lambda s: int(round(s * fps))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    W, H = info["w"], info["h"]
    src_frames = F(info["dur"]); src_name = os.path.basename(src)

    # tagli: ogni pezzo tenuto è una clip intera su V1
    keeps = []
    t = 0
    for a, b in sorted(ed["keep"]):
        i0, i1 = F(a), F(b)
        if i1 > i0:
            keeps.append((t, i0, i1, a, b)); t += i1 - i0
    total = t

    def tl_of(x):
        """posizione in timeline (fotogrammi) di un istante della sorgente"""
        for st, i0, i1, a, b in keeps:
            if a - 1e-6 <= x <= b + 1e-6:
                return st + min(max(F(x), i0), i1) - i0
        return None

    # zoom: uno per pezzo tenuto (spezzato solo dove c'è un taglio), più le entrate/uscite animate
    zclips, fxclips = [], []
    for a, b, lv, kind in subpieces(build_pieces(ed["keep"], ed["zooms"]), ed):
        if not lv:
            continue
        s0, s1 = tl_of(a), tl_of(b)
        if s0 is None or s1 is None or s1 <= s0:
            continue
        i0 = F(a)
        last = zclips[-1] if zclips else None
        if last and last["lv"] == lv and last["end"] == s0 and last["out"] == i0:
            last["end"] = s1; last["out"] = i0 + (s1 - s0)
        else:
            zclips.append({"start": s0, "end": s1, "in": i0, "out": i0 + (s1 - s0), "lv": lv})
        if kind != "static":
            fxclips.append((s0, s1, kind, a, b))

    defined = set()

    def file_el(fid, path, nm, frames, audio=True):
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        aud = (f"<audio><samplecharacteristics><depth>16</depth><samplerate>{info['sr']}</samplerate>"
               f"</samplecharacteristics><channelcount>{info['ch']}</channelcount></audio>") if audio else ""
        return (f'<file id="{fid}"><name>{escape(nm)}</name><pathurl>{escape(path_url(path))}</pathurl>{rate}'
                f"<duration>{frames}</duration><timecode>{rate}<string>00:00:00:00</string><frame>0</frame>"
                f"<displayformat>NDF</displayformat></timecode><media><video><samplecharacteristics>{rate}"
                f"<width>{W}</width><height>{H}</height><anamorphic>FALSE</anamorphic>"
                f"<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
                f"</samplecharacteristics></video>{aud}</media></file>")

    def motion(lv):
        x0, y0, w, h, s = view(info, cam, lv, ed["zoom"])
        ax = W / 2 + (W / 2 - (x0 + w / 2)) * s
        ay = H / 2 + (H / 2 - (y0 + h / 2)) * s
        return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
                "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                f"<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax>"
                f"<value>{s * 100:.3f}</value></parameter><parameter><parameterid>center</parameterid><name>Center</name>"
                f"<value><horiz>{(ax - W / 2) / W:.6f}</horiz><vert>{(ay - H / 2) / H:.6f}</vert></value></parameter>"
                "</effect></filter>")

    # audio: pezzi di A1 per ogni clip di V1, con i buchi sulle parolacce (il suono va su A2)
    cens = [c for c in (ed.get("censor") or []) if c.get("on", True)]
    has_audio = info.get("has_audio", True)
    apieces, beeps, vlinks = [], [], []
    for k, (st, i0, i1, a, b) in enumerate(keeps):
        mine = []
        if has_audio:
            holes = sorted((max(F(c["s"]), i0), min(F(c["e"]), i1)) for c in cens if F(c["e"]) > i0 and F(c["s"]) < i1)
            cur = i0
            for h0, h1 in holes:
                if h0 > cur:
                    mine.append(len(apieces)); apieces.append((st + cur - i0, cur, h0, k))
                if h1 > h0:
                    beeps.append((st + h0 - i0, h1 - h0))
                cur = max(cur, h1)
            if i1 > cur:
                mine.append(len(apieces)); apieces.append((st + cur - i0, cur, i1, k))
        vlinks.append(mine)

    def link(cid, media, track, idx):
        return (f"<link><linkclipref>{cid}</linkclipref><mediatype>{media}</mediatype>"
                f"<trackindex>{track}</trackindex><clipindex>{idx + 1}</clipindex></link>")

    labels = {1: "ZOOM CAM", 2: "CAM PIENA"}
    x = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE xmeml>", '<xmeml version="4">',
         f"<sequence><name>{escape(name)}</name>{rate}<duration>{total}</duration>"
         f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
         f"<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>"
         "<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
         "</samplecharacteristics></format><track>"]
    for k, (st, i0, i1, a, b) in enumerate(keeps):
        links = link(f"v{k}", "video", 1, k) + "".join(link(f"a{j}", "audio", 1, j) for j in vlinks[k])
        x.append(f'<clipitem id="v{k}"><name>{escape(src_name)}</name><enabled>TRUE</enabled>'
                 f"<duration>{src_frames}</duration>{rate}<start>{st}</start><end>{st + i1 - i0}</end>"
                 f"<in>{i0}</in><out>{i1}</out>" + file_el("src", src, src_name, src_frames) + links + "</clipitem>")
    x.append("</track><track>")
    for k, z in enumerate(zclips):
        x.append(f'<clipitem id="z{k}"><name>{labels.get(z["lv"], "ZOOM")}</name><enabled>TRUE</enabled>'
                 f"<duration>{src_frames}</duration>{rate}<start>{z['start']}</start><end>{z['end']}</end>"
                 f"<in>{z['in']}</in><out>{z['out']}</out>" + file_el("src", src, src_name, src_frames) + motion(z["lv"]) + "</clipitem>")
    x.append("</track>")
    if fx:
        x.append("<track>")
        for k, (s0, s1, kind, a, b) in enumerate(fxclips):
            fp = fx.get((round(a, 3), round(b, 3)))
            if not fp:
                continue
            d = s1 - s0
            x.append(f'<clipitem id="fx{k}"><name>{"ZOOM IN" if kind == "in" else "ZOOM OUT"} (motion blur)</name>'
                     f"<enabled>TRUE</enabled><duration>{d}</duration>{rate}<start>{s0}</start><end>{s1}</end>"
                     f"<in>0</in><out>{d}</out>" + file_el(f"fx{k}", fp, os.path.basename(fp), d, audio=False) + "</clipitem>")
        x.append("</track>")
    x.append("</video><audio>")
    if has_audio:
        x.append('<track premiereTrackType="Stereo">' if info.get("ch", 2) >= 2 else "<track>")
        for j, (st, i0, i1, k) in enumerate(apieces):
            links = link(f"v{k}", "video", 1, k) + "".join(link(f"a{m}", "audio", 1, m) for m in vlinks[k])
            x.append(f'<clipitem id="a{j}"><name>{escape(src_name)}</name><enabled>TRUE</enabled>'
                     f"<duration>{src_frames}</duration>{rate}<start>{st}</start><end>{st + i1 - i0}</end>"
                     f'<in>{i0}</in><out>{i1}</out><file id="src"/><sourcetrack><mediatype>audio</mediatype>'
                     f"<trackindex>1</trackindex></sourcetrack>{links}</clipitem>")
        x.append("</track>")
    if beeps and bip:
        bfr = F(3.0)
        x.append("<track>")
        for k, (st, ln) in enumerate(beeps):
            ln = max(1, min(ln, bfr))
            fe = (f'<file id="bip"><name>censura.wav</name><pathurl>{escape(path_url(bip))}</pathurl>{rate}<duration>{bfr}</duration>'
                  f"<media><audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
                  f"<channelcount>2</channelcount></audio></media></file>") if k == 0 else '<file id="bip"/>'
            x.append(f'<clipitem id="bp{k}"><name>CENSURA</name><enabled>TRUE</enabled><duration>{bfr}</duration>{rate}'
                     f"<start>{st}</start><end>{st + ln}</end><in>0</in><out>{ln}</out>{fe}"
                     "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
        x.append("</track>")
    x.append("</audio></media></sequence></xmeml>")
    with open(out_xml, "w", encoding="utf-8") as f:
        f.write("".join(x))
    return out_xml
