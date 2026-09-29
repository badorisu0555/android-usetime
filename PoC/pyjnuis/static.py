from jnius import autoclass

Math = autoclass("java.lang.Math")
Integer = autoclass("java.lang.Integer")

print("大きいほう：",Math.max(45,12))

print("intの最大値：",Integer.MAX_VALUE)