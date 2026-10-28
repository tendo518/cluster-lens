"""CSV presentation of history results, without HTTP or storage access."""

import csv
import io


def history_csv(history):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "服务器",
            "开始时间(Unix秒)",
            "结束时间(Unix秒)",
            "样本数",
            "采集覆盖率(%)",
            "观测在线率(%)",
            "GPU平均使用率(%)",
            "GPU单卡峰值(%)",
            "显存平均使用率(%)",
            "CPU平均使用率(%)",
            "内存平均使用率(%)",
            "繁忙GPU卡时(>=10%)",
            "有效观测GPU卡时",
        ]
    )
    for row in history["stats"]:
        name = row["host"]
        # Avoid spreadsheet formula injection from a locally configured hostname.
        if name.startswith(("=", "+", "-", "@", "\t", "\r")):
            name = "'" + name
        values = [
            row.get(key)
            for key in (
                "samples",
                "coverage",
                "availability",
                "avg_gpu",
                "peak_gpu",
                "avg_gpu_memory",
                "avg_cpu",
                "avg_memory",
                "busy_gpu_hours",
                "observed_gpu_hours",
            )
        ]
        writer.writerow(
            [
                name,
                history["start"],
                history["end"],
                *[
                    round(value, 3) if isinstance(value, float) else value
                    for value in values
                ],
            ]
        )
    return ("\ufeff" + stream.getvalue()).encode()
