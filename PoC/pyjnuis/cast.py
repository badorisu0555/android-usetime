from jnius import autoclass ,cast

ArrayList = autoclass("java.util.ArrayList")
apps = ArrayList()
apps.add("Youtube")

as_collection = cast("java.util.Collection", apps)

print("Collectionとしてみた件数：",as_collection.size())

apps.add("LINE")
print("追加後の件数：", as_collection.size())