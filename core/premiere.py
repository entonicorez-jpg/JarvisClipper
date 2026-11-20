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


def build_autoedit_xml(pl, src, info, cam, out_xml, name="Montaggio Jarvis"):
    """Sequenza orizzontale: V1 = pezzi tagliati con zoom (Movimento: scala + posizione), A1/A2 = audio."""
    from .autoedit import view
    fps = info["fps"]
    tb = int(round(fps)); ntsc = "TRUE" if abs(fps - tb) > 0.01 else "FALSE"
    F = lambda s: int(round(s * fps))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    W, H = info["w"], info["h"]
    src_frames = F(info["dur"]); src_name = os.path.basename(src)
    items, t = [], 0
    for a, b, lv in pl["pieces"]:
        i0, i1 = F(a), F(b)
        if i1 <= i0:
            continue
        items.append((t, i0, i1, lv)); t += i1 - i0
    total = t
    file_full = (f'<file id="src"><name>{escape(src_name)}</name><pathurl>{escape(path_url(src))}</pathurl>{rate}'
                 f"<duration>{src_frames}</duration><timecode>{rate}<string>00:00:00:00</string><frame>0</frame>"
                 f"<displayformat>NDF</displayformat></timecode><media><video><samplecharacteristics>{rate}"
                 f"<width>{W}</width><height>{H}</height><anamorphic>FALSE</anamorphic>"
                 f"<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
                 f"</samplecharacteristics></video><audio><samplecharacteristics><depth>16</depth>"
                 f"<samplerate>{info['sr']}</samplerate></samplecharacteristics><channelcount>{info['ch']}</channelcount>"
                 f"</audio></media></file>")
    labels = {0: "", 1: " [ZOOM CAM]", 2: " [CAM PIENA]"}
    x = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE xmeml>", '<xmeml version="4">',
         f"<sequence><name>{escape(name)}</name>{rate}<duration>{total}</duration>"
         f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
         f"<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>"
         "<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>"
         "</samplecharacteristics></format><track>"]
    first = True
    for k, (st, i0, i1, lv) in enumerate(items):
        eff = ""
        if lv:
            x0, y0, w, h, s = view(info, cam, lv, pl["zoom"])
            # il centro della regione mostrata va al centro dello schermo
            ax = W / 2 + (W / 2 - (x0 + w / 2)) * s
            ay = H / 2 + (H / 2 - (y0 + h / 2)) * s
            eff = ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
                   "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                   f"<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax>"
                   f"<value>{s * 100:.3f}</value></parameter><parameter><parameterid>center</parameterid><name>Center</name>"
                   f"<value><horiz>{(ax - W / 2) / W:.6f}</horiz><vert>{(ay - H / 2) / H:.6f}</vert></value></parameter>"
                   "</effect></filter>")
        x.append(f'<clipitem id="v{k}"><name>{escape(src_name + labels[lv])}</name><enabled>TRUE</enabled>'
                 f"<duration>{src_frames}</duration>{rate}<start>{st}</start><end>{st + i1 - i0}</end>"
                 f"<in>{i0}</in><out>{i1}</out>" + (file_full if first else '<file id="src"/>') + eff + "</clipitem>")
        first = False
    x.append("</track></video><audio>")
    if info.get("has_audio", True):
        for ch in range(1, min(info["ch"], 2) + 1):
            x.append("<track>")
            for k, (st, i0, i1, lv) in enumerate(items):
                x.append(f'<clipitem id="a{ch}_{k}"><name>{escape(src_name)}</name><enabled>TRUE</enabled>'
                         f"<duration>{src_frames}</duration>{rate}<start>{st}</start><end>{st + i1 - i0}</end>"
                         f'<in>{i0}</in><out>{i1}</out><file id="src"/><sourcetrack><mediatype>audio</mediatype>'
                         f"<trackindex>{ch}</trackindex></sourcetrack></clipitem>")
            x.append("</track>")
    x.append("</audio></media></sequence></xmeml>")
    with open(out_xml, "w", encoding="utf-8") as f:
        f.write("".join(x))
    return out_xml
