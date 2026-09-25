import io

import segno


def qr_png(payload: str) -> bytes:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="png", scale=8, border=2)
    return buf.getvalue()
