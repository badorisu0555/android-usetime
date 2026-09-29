from jnius import autoclass

# autoclass("Javaのクラスの正式名") で、そのクラスをPythonから触れるようにする。
# この時点ではまだ何も実行していない。「使う準備」だけ。
System = autoclass("java.lang.System")

# Javaで書くと System.out.println("...") と同じ呼び出し。
# 表示しているのはPythonのprintではなく、Java側の出力機能。
System.out.println("Hello from Java!")

# 比較用：こちらは普通のPythonのprint
print('Hello from Python!')