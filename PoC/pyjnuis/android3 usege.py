from datetime import datetime
from jnius import autoclass, cast

# ---- 手順⑥：自分のアプリをつかむ ----
PythonActivity = autoclass('org.kivy.android.PythonActivity')
Context = autoclass('android.content.Context')
UsageStatsManager = autoclass('android.app.usage.UsageStatsManager')
activity = PythonActivity.mActivity

# ---- 手順⑦：記録係を借りる（④のcast） ----
usm = cast(
    'android.app.usage.UsageStatsManager',
    activity.getSystemService(Context.USAGE_STATS_SERVICE)
)

# ---- ⑤で練習したミリ秒変換：期間を「今日の0時〜今」にする ----
midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
start_ms = int(midnight.timestamp() * 1000)
now_ms = int(datetime.now().timestamp() * 1000)

# ---- ここが唯一、Androidに利用時間を問い合わせている行 ----
# INTERVAL_DAILY は③と同じstatic定数で、「1日単位で集計して」という指定
stats_list = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start_ms, now_ms)

# ---- ②で練習した「Javaのリストをfor文で回す」 ----
print('=== 今日のアプリ別利用時間 ===')
for stat in stats_list:
    pkg = stat.getPackageName()                  # アプリの内部名
    used_ms = stat.getTotalTimeInForeground()    # 画面に出ていた合計時間（ミリ秒）
    minutes = used_ms / 1000 / 60                # ミリ秒 → 分
    if minutes >= 1:                             # 1分未満は見づらいので省く
        print(f'{pkg}: {minutes:.1f}分')