from jnius import autoclass

# Javaの ArrayList（可変長のリスト）クラスを使えるようにする
ArrayList = autoclass("java.util.ArrayList")
# Pythonのクラスと同じように () を付けると、Java側で new ArrayList() が実行される
apps = ArrayList()

# Javaのメソッド add() を呼ぶ。引数のPythonの文字列は、jniusがJavaの文字列に自動変換する
apps.add("Youtube")
apps.add("Instagram")
apps.add("LINE")

# size() はJavaのメソッド。戻り値のJavaのintは、Pythonのintとして受け取れる
print("件数：", apps.size())

# get(i) もJavaのメソッド。Javaの文字列はPythonのstrとして戻ってくる
print("先頭：",apps.get(0))

# Javaのリストも、Pythonのfor文でそのまま回せる
#（後でAndroidの利用時間の一覧を回すときも、この書き方をそのまま使う）
for name in apps:
    print('-', name)