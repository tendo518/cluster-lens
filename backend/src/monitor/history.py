"""History range rules and presentation of aggregate facts, without SQL or HTTP."""

import math


def history_range(start, end, now):
    try:
        start = float(now - 3600 if start is None else start)
        end = float(now if end is None else end)
    except (ValueError, TypeError):
        raise ValueError("start / end 必须是 Unix 秒时间戳") from None
    if not all(math.isfinite(value) for value in (start, end)):
        raise ValueError("时间必须为有限数值")
    if start < 0 or end <= start or end - start > 31 * 86400 or end > now + 60:
        raise ValueError("请选择有效时间范围（不超过 31 天，结束时间不能晚于当前时间）")
    return start, end


def history_bucket(start, end, interval):
    return max(interval, math.ceil((end - start) / 180 / interval) * interval)


def assemble_history(start, end, interval, bucket, names, series, rows, total):
    """Derive coverage and null gaps without inventing observations or GPU hours."""

    def finish(row, host_count=1):
        result = dict(row)
        observed = result.get("observed_seconds") or 0
        result["availability"] = (
            100 * (result.get("online_seconds") or 0) / observed if observed else None
        )
        result["coverage"] = min(
            100, 100 * observed / ((end - start) * max(1, host_count))
        )
        for key in ("busy_gpu_hours", "observed_gpu_hours"):
            result[key] = result.get(key) or 0
        return result

    by_host = {row["host"]: finish(row) for row in rows}
    stats = [
        by_host.get(
            name,
            {
                "host": name,
                "samples": 0,
                "coverage": 0,
                "availability": None,
                "busy_gpu_hours": 0,
                "observed_gpu_hours": 0,
            },
        )
        for name in names
    ]
    indexed = {row["bucket"]: row for row in series}
    points = []
    if series:
        for index in range(math.ceil((end - start) / bucket)):
            row = indexed.get(index, {})
            points.append(
                {
                    "ts": start
                    + index * bucket
                    + min(bucket, end - start - index * bucket) / 2,
                    **{
                        key: row.get(key)
                        for key in ("cpu", "memory", "gpu", "gpu_memory")
                    },
                }
            )
    return {
        "start": start,
        "end": end,
        "bucket_seconds": bucket,
        "points": points,
        "stats": stats,
        "aggregate": finish(total, len(names)),
        "busy_threshold": 10,
        "sample_interval": interval,
    }
