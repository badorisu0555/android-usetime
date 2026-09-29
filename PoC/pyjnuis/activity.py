from jnius import autoclass

# PythonActivity は「今Pythonを動かしているアプリの画面」への参照を持っているクラス。
# もともとはKivy（python-for-android）のお作法で決まった名前で、
# 多くのjniusのサンプルや周辺ライブラリがこの名前を前提にしている。
PythonActivity = autoclass('org.kivy.android.PythonActivity')

# mActivity は static な変数。③で練習した「クラス名.名前」の読み方と同じ。
activity = PythonActivity.mActivity

# Androidのアプリには必ず「パッケージ名」という内部名がある。
# ここで表示されれば、PythonからAndroidのJavaの世界に手が届いている証拠。
print('このアプリのパッケージ名:', activity.getPackageName())