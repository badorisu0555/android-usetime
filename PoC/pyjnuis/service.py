from jnius import autoclass, cast

PythonActivity = autoclass('org.kivy.android.PythonActivity')
Context = autoclass('android.content.Context')
activity = PythonActivity.mActivity

# getSystemService(名前) は「このOS機能を貸してください」とAndroidに頼むメソッド。
# Context.USAGE_STATS_SERVICE は③で練習したstatic定数で、中身は機能の名前（文字列）。
raw = activity.getSystemService(Context.USAGE_STATS_SERVICE)

# 戻り値は「何でも入る汎用の型（Object）」として返ってくる。
# ④で練習したcastで、「これは UsageStatsManager だ」とjniusに教える。
usm = cast('android.app.usage.UsageStatsManager', raw)

print('借りられた記録係:', usm)