"""Export del montaggio come progetto Kdenlive (.kdenlive, formato documento 1.1).

Tracce (dall'alto):
  V2  entrate/uscite degli zoom già renderizzate con il motion blur (clip corte, spostabili o cancellabili)
  V1  i pezzi tagliati; gli zoom sono l'effetto "Trasforma" con keyframe (modificabile)
  A1  la tua voce, abbassata a zero sulle parolacce (effetto Volume con keyframe)
  A2  il suono di censura
Audio e video di ogni pezzo sono raggruppati (AVSplit), come quando tagli in Kdenlive.
"""
import hashlib, json, os, time, uuid
from fractions import Fraction
from xml.sax.saxutils import escape
from .autoedit import view, subpieces, build_pieces

KDEN_VERSION = "26.08.3"


def file_hash(path):
    """Stesso calcolo di Kdenlive: md5 del primo e dell'ultimo MB (o del file intero se è piccolo)."""
    size = os.path.getsize(path)
    h = hashlib.md5()
    with open(path, "rb") as f:
        if size > 2_000_000:
            h.update(f.read(1_000_000)); f.seek(size - 1_000_000); h.update(f.read(1_000_000))
        else:
            h.update(f.read())
    return h.hexdigest(), size


def fps_fraction(fps):
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001), (120000, 1001)):
        if abs(fps - num / den) < 0.01:
            return num, den
    r = Fraction(fps).limit_denominator(1001)
    return r.numerator, r.denominator


def _p(name, value):
    return f'  <property name="{name}">{escape(str(value))}</property>\n'


class Doc:
    def __init__(self, info):
        self.num, self.den = fps_fraction(info["fps"])
        self.fps = self.num / self.den
        self.parts, self.n = [], 0

    def F(self, s):
        return int(round(s * self.fps))

    def tc(self, frames):
        """Tempo per i keyframe (formato orologio di MLT/Kdenlive)."""
        s = frames / self.fps
        h, rem = divmod(s, 3600); m, sec = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"

    def nid(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"


def _chain(cid, path, kid, ctype, frames, info=None, audio=True, video=True, role=None, folder=-1):
    h, size = file_hash(path)
    x = f' <chain id="{cid}" out="{frames - 1}">\n'
    x += _p("length", frames) + _p("eof", "pause") + _p("resource", path) + _p("mlt_service", "avformat-novalidate")
    x += _p("seekable", 1) + _p("audio_index", 1 if (audio and video) else (0 if audio else -1)) + _p("video_index", 0 if video else -1)
    x += _p("kdenlive:clipname", "") + _p("kdenlive:clip_type", ctype) + _p("kdenlive:folderid", folder) + _p("kdenlive:id", kid)
    x += _p("mute_on_pause", 0) + _p("kdenlive:file_size", size) + _p("kdenlive:file_hash", h)
    if role == "a":
        x += _p("set.test_audio", 0) + _p("set.test_image", 1)
    elif role == "v":
        x += _p("set.test_audio", 1) + _p("set.test_image", 0)
    return x + " </chain>\n"


def _track(doc, pl_id, entries, audio, name):
    """Una traccia Kdenlive = tractor con due playlist (la seconda vuota)."""
    x = f' <playlist id="{pl_id}">\n'
    if audio:
        x += _p("kdenlive:audio_track", 1)
    x += _p("kdenlive:track_name", name)
    x += "".join(entries)
    x += " </playlist>\n"
    x += f' <playlist id="{pl_id}b">\n' + (_p("kdenlive:audio_track", 1) if audio else "") + " </playlist>\n"
    tid = pl_id.replace("pl", "tr")
    x += f' <tractor id="{tid}" in="0">\n'
    if audio:
        x += _p("kdenlive:audio_track", 1)
    x += _p("kdenlive:trackheight", 67) + _p("kdenlive:timeline_active", 1) + _p("kdenlive:collapsed", 0)
    x += _p("kdenlive:thumbs_format", "") + _p("kdenlive:audio_rec", "")
    hide = "video" if audio else "audio"
    x += f'  <track hide="{hide}" producer="{pl_id}"/>\n  <track hide="{hide}" producer="{pl_id}b"/>\n'
    if audio:
        x += ('  <filter><property name="window">75</property><property name="max_gain">20dB</property>'
              '<property name="mlt_service">volume</property><property name="internal_added">237</property>'
              '<property name="disable">1</property></filter>\n'
              '  <filter><property name="channel">-1</property><property name="mlt_service">panner</property>'
              '<property name="internal_added">237</property><property name="start">0.5</property>'
              '<property name="disable">1</property></filter>\n')
    x += " </tractor>\n"
    return tid, x


def build(ed, src, info, cam, out_path, name="Montaggio Jarvis", fx=None, sfx=None):
    """Scrive il progetto .kdenlive. fx = {(a, b): file} entrate/uscite zoom; sfx = file del suono di censura."""
    doc = Doc(info)
    W, H = info["w"], info["h"]
    F = doc.F
    src = os.path.abspath(src)
    src_frames = F(info["dur"])
    subs = [(a, b, lv, k) for a, b, lv, k in subpieces(build_pieces(ed["keep"], ed["zooms"]), ed) if F(b) > F(a)]
    cens = [c for c in (ed.get("censor") or []) if c.get("on", True)]

    v1, a1, v2, a2, groups = [], [], [], [], []
    pos = 0
    v2_pos = a2_pos = 0

    def rect(lv):
        x0, y0, w, h, s = view(info, cam, lv, ed["zoom"])
        return f"{-x0 * s:.2f} {-y0 * s:.2f} {W * s:.2f} {H * s:.2f} 1.000000"

    full = f"0 0 {W} {H} 1.000000"
    for a, b, lv, kind in subs:
        i0, i1 = F(a), F(b)
        d = i1 - i0
        filt = ""
        if lv:
            last = doc.tc(max(0, d - 1))
            if kind == "in":
                anim = f"00:00:00.000={full};{last}={rect(lv)}"
            elif kind == "out":
                anim = f"00:00:00.000={rect(lv)};{last}={full}"
            else:
                anim = f"00:00:00.000={rect(lv)}"
            filt = (f'   <filter id="{doc.nid("zoom")}">\n    <property name="rotate_center">1</property>\n'
                    f'    <property name="mlt_service">qtblend</property>\n    <property name="kdenlive_id">qtblend</property>\n'
                    f'    <property name="rect">{anim}</property>\n    <property name="rotation">00:00:00.000=0</property>\n'
                    f'    <property name="compositing">0</property>\n    <property name="distort">0</property>\n'
                    f'    <property name="kdenlive:collapsed">0</property>\n   </filter>\n')
        v1.append(f'  <entry producer="src_v" in="{i0}" out="{i1 - 1}">\n   <property name="kdenlive:id">4</property>\n{filt}  </entry>\n')
        # voce: volume a zero sulle parolacce di questo pezzo
        vol = ""
        hits = [(max(F(c["s"]), i0) - i0, min(F(c["e"]), i1) - i0) for c in cens if F(c["e"]) > i0 and F(c["s"]) < i1]
        if hits:
            kf = ["00:00:00.000=0"]
            for h0, h1 in hits:
                kf.append(f"{doc.tc(h0)}|=-100")
                kf.append(f"{doc.tc(h1)}|=0")
            vol = (f'   <filter id="{doc.nid("mute")}">\n    <property name="window">75</property>\n    <property name="max_gain">20dB</property>\n'
                   f'    <property name="mlt_service">volume</property>\n    <property name="kdenlive_id">volume</property>\n'
                   f'    <property name="level">{";".join(kf)}</property>\n    <property name="kdenlive:collapsed">0</property>\n   </filter>\n')
        a1.append(f'  <entry producer="src_a" in="{i0}" out="{i1 - 1}">\n   <property name="kdenlive:id">4</property>\n{vol}  </entry>\n')
        groups.append({"children": [{"data": f"2:{pos}", "leaf": "clip", "type": "Leaf"},
                                    {"data": f"1:{pos}", "leaf": "clip", "type": "Leaf"}], "type": "AVSplit"})
        # V2: entrata/uscita dello zoom con il motion blur
        fp = fx.get((round(a, 3), round(b, 3))) if (fx and kind != "static") else None
        if fp:
            if pos > v2_pos:
                v2.append(f'  <blank length="{pos - v2_pos}"/>\n')
            fid = "fx" + str(sum(isinstance(e, tuple) for e in v2) + 1)
            v2.append((fid, fp, d))
            v2_pos = pos + d
        # A2: suono di censura
        for h0, h1 in hits:
            if not sfx:
                break
            st = pos + h0
            ln = max(1, min(h1 - h0, F(3.0)))
            if st > a2_pos:
                a2.append(f'  <blank length="{st - a2_pos}"/>\n')
            elif st < a2_pos:
                continue
            a2.append(f'  <entry producer="sfx_a" in="0" out="{ln - 1}">\n   <property name="kdenlive:id">5</property>\n  </entry>\n')
            a2_pos = st + ln
        pos += d
    total = pos

    # clip nel contenitore (bin)
    chains = _chain("src_bin", src, 4, 0, src_frames) + _chain("src_v", src, 4, 0, src_frames, role="v") + _chain("src_a", src, 4, 0, src_frames, role="a")
    bin_entries = [f'  <entry producer="src_bin" in="0" out="{src_frames - 1}"/>\n']
    if sfx and os.path.exists(sfx):
        sf = F(3.0)
        chains += _chain("sfx_bin", os.path.abspath(sfx), 5, 1, sf, video=False) + _chain("sfx_a", os.path.abspath(sfx), 5, 1, sf, video=False, role="a")
        bin_entries.append(f'  <entry producer="sfx_bin" in="0" out="{sf - 1}"/>\n')
    v2_xml, kid = [], 10
    for e in v2:
        if isinstance(e, tuple):
            fid, fp, d = e
            kid += 1
            chains += _chain(f"{fid}_bin", os.path.abspath(fp), kid, 2, d, audio=False, folder=7)
            chains += _chain(f"{fid}_v", os.path.abspath(fp), kid, 2, d, audio=False, role="v", folder=7)
            bin_entries.append(f'  <entry producer="{fid}_bin" in="0" out="{d - 1}"/>\n')
            v2_xml.append(f'  <entry producer="{fid}_v" in="0" out="{d - 1}">\n   <property name="kdenlive:id">{kid}</property>\n  </entry>\n')
        else:
            v2_xml.append(e)

    seq_uuid = "{" + str(uuid.uuid4()) + "}"
    num, den = doc.num, doc.den
    from math import gcd
    g = gcd(W, H)
    x = ['<?xml version=\'1.0\' encoding=\'utf-8\'?>\n<mlt LC_NUMERIC="C" producer="main_bin" version="7.22.0" root="">\n',
         f' <profile frame_rate_num="{num}" sample_aspect_num="1" display_aspect_den="{H // g}" colorspace="709" progressive="1" '
         f'description="Jarvis {W}x{H} {doc.fps:.2f} fps" display_aspect_num="{W // g}" frame_rate_den="{den}" width="{W}" height="{H}" sample_aspect_den="1"/>\n',
         chains,
         f' <producer id="black_track" in="0" out="{max(total, 1) - 1}">\n' + _p("length", 2147483647) + _p("eof", "continue")
         + _p("resource", "black") + _p("aspect_ratio", 1) + _p("mlt_service", "color") + _p("kdenlive:playlistid", "black_track")
         + _p("mlt_image_format", "rgba") + _p("set.test_audio", 0) + " </producer>\n"]
    tA2, xa2 = _track(doc, "pl_a2", a2, True, "A2 censura")
    tA1, xa1 = _track(doc, "pl_a1", a1, True, "A1 voce")
    tV1, xv1 = _track(doc, "pl_v1", v1, False, "V1 montaggio")
    tV2, xv2 = _track(doc, "pl_v2", v2_xml, False, "V2 zoom blur")
    x += [xa2, xa1, xv1, xv2]
    seq = f' <tractor id="sequence" in="0" out="{max(total, 1) - 1}">\n'
    for k, v in [("kdenlive:duration", ""), ("kdenlive:maxduration", total), ("kdenlive:clipname", name), ("kdenlive:description", ""),
                 ("kdenlive:uuid", seq_uuid), ("kdenlive:producer_type", 17), ("kdenlive:id", 3), ("kdenlive:clip_type", 0),
                 ("kdenlive:folderid", 2), ("kdenlive:sequenceproperties.activeTrack", 2), ("kdenlive:sequenceproperties.audioChannels", 2),
                 ("kdenlive:sequenceproperties.audioTarget", 1), ("kdenlive:sequenceproperties.disablepreview", 0),
                 ("kdenlive:sequenceproperties.documentuuid", seq_uuid), ("kdenlive:sequenceproperties.hasAudio", 1),
                 ("kdenlive:sequenceproperties.hasVideo", 1), ("kdenlive:sequenceproperties.position", 0),
                 ("kdenlive:sequenceproperties.scrollPos", 0), ("kdenlive:sequenceproperties.tracks", 4),
                 ("kdenlive:sequenceproperties.tracksCount", 4), ("kdenlive:sequenceproperties.verticalzoom", 1),
                 ("kdenlive:sequenceproperties.videoTarget", 2), ("kdenlive:sequenceproperties.zonein", 0),
                 ("kdenlive:sequenceproperties.zoneout", total), ("kdenlive:sequenceproperties.zoom", 8),
                 ("kdenlive:sequenceproperties.groups", json.dumps(groups, indent=4)), ("kdenlive:sequenceproperties.guides", "[\n]\n")]:
        seq += _p(k, v)
    seq += f'  <track producer="black_track"/>\n  <track producer="{tA2}"/>\n  <track producer="{tA1}"/>\n  <track producer="{tV1}"/>\n  <track producer="{tV2}"/>\n'
    for i, (svc, b) in enumerate([("mix", 1), ("mix", 2), ("qtblend", 3), ("qtblend", 4)]):
        if svc == "mix":
            seq += (f'  <transition id="tr_mix{b}"><property name="a_track">0</property><property name="b_track">{b}</property>'
                    '<property name="mlt_service">mix</property><property name="kdenlive_id">mix</property><property name="internal_added">237</property>'
                    '<property name="always_active">1</property><property name="accepts_blanks">1</property><property name="sum">1</property></transition>\n')
        else:
            seq += (f'  <transition id="tr_comp{b}"><property name="a_track">0</property><property name="b_track">{b}</property>'
                    '<property name="compositing">0</property><property name="distort">0</property><property name="rotate_center">0</property>'
                    '<property name="mlt_service">qtblend</property><property name="always_active">1</property><property name="internal_added">237</property></transition>\n')
    seq += " </tractor>\n"
    x.append(seq)
    mb = ' <playlist id="main_bin">\n' + _p("kdenlive:folder.-1.2", "Sequences") + _p("kdenlive:folder.-1.7", "Zoom blur (Jarvis)")
    for k, v in [("activetimeline", seq_uuid), ("audioChannels", 2), ("browserurl", ""), ("compositing", 1),
                 ("documentid", int(time.time() * 1000)), ("enableTimelineZone", 0), ("enableexternalproxy", 0), ("enableproxy", 0),
                 ("generateimageproxy", 0), ("generateproxy", 0), ("kdenliveversion", KDEN_VERSION), ("opensequences", seq_uuid),
                 ("proxyimageminsize", 2000), ("proxyimagesize", 800), ("proxyminsize", 1000), ("proxyresize", 640),
                 ("seekOffset", 30000), ("uuid", seq_uuid), ("version", "1.1")]:
        mb += _p(f"kdenlive:docproperties.{k}", v)
    mb += _p("kdenlive:expandedFolders", "") + _p("kdenlive:binZoom", 4) + _p("kdenlive:documentnotes", "Montato con Jarvis Clipper") + _p("xml_retain", 1)
    mb += "".join(bin_entries) + f'  <entry producer="sequence" in="0" out="{max(total, 1) - 1}"/>\n </playlist>\n'
    x.append(mb)
    x.append(f' <tractor id="project" in="0" out="{max(total, 1) - 1}">\n' + _p("kdenlive:projectTractor", 1)
             + f'  <track producer="sequence" in="0" out="{max(total, 1) - 1}"/>\n </tractor>\n</mlt>\n')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(x))
    return {"path": out_path, "clips": len(subs), "zoom_fx": sum(1 for e in v2 if isinstance(e, tuple)),
            "bip": sum(1 for e in a2 if "<entry" in e), "frames": total}
