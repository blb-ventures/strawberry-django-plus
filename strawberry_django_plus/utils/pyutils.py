from typing import Dict, Mapping

from typing_extensions import TypeAlias

DictTree: TypeAlias = Dict[str, "DictTree"]


def dicttree_merge(dict1: DictTree, dict2: DictTree) -> DictTree:
    new = {
        **dict1,
        **dict2,
    }

    for k, v1 in dict1.items():
        if not isinstance(v1, Mapping):
            continue

        v2 = dict2.get(k)
        if isinstance(v2, Mapping):
            new[k] = dicttree_merge(v1, v2)

    for k, v2 in dict2.items():
        if k in new or not isinstance(v2, Mapping):
            continue

        v1 = dict1.get(k)
        if isinstance(v1, Mapping):
            new[k] = dicttree_merge(v1, v2)

    return new


def dicttree_intersect_diff(dict1: DictTree, dict2: DictTree) -> bool:
    for k in set(dict1) & set(dict2):
        v1 = dict1[k]
        v2 = dict2[k]

        if isinstance(v1, Mapping) and isinstance(v2, Mapping):
            v_intersect = dicttree_intersect_diff(v1, v2)
            if v_intersect:
                return True

        return v1 != v2

    return False
