import time
from datetime import datetime
from jnius import autoclass

System = autoclass("java.lang.System")

java_now_ms = System.currentTimeMillis()

python_now_ms = int(time.time() * 1000)

print("Java :",java_now_ms)
print("Python :",python_now_ms)
print("差分 :",java_now_ms - python_now_ms)

print("読める形：",datetime.fromtimestamp(java_now_ms / 1000))

midnight = datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
print("今日の0時：",midnight.timestamp() * 1000)