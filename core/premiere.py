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
