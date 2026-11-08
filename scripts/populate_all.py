import sys
from assetrag.library import Library
from assetrag.populate import populate
only = set(sys.argv[1:]) or None
lib = Library()
print(populate(lib, only=only), flush=True)
s = lib.stats()
print(s["total"], s["by_category"], flush=True)
print(s["rejected_counts"], flush=True)
