/**
 * Safari / iOS WebKit は option・optgroup の hidden を無視し、
 * disabled でもグレーアウトしたまま選択肢に残す。
 * 非表示にしたい optgroup は DOM から外し、再表示時に元の順で戻す。
 */
(function (global) {
    'use strict';

    const cachedGroupsBySelect = new WeakMap();

    function getCachedOptGroups(mapSelect) {
        let groups = cachedGroupsBySelect.get(mapSelect);
        if (!groups) {
            groups = Array.from(mapSelect.querySelectorAll('optgroup'));
            cachedGroupsBySelect.set(mapSelect, groups);
        }
        return groups;
    }

    function filterMapOptgroupsByMode(modeSelect, mapSelect) {
        if (!modeSelect || !mapSelect) {
            return;
        }
        const selectedMode = modeSelect.value;
        const selectedOption = mapSelect.selectedOptions[0];
        if (selectedOption && selectedOption.dataset.modeId && selectedMode && selectedOption.dataset.modeId !== selectedMode) {
            mapSelect.value = '';
        }
        getCachedOptGroups(mapSelect).forEach((group) => {
            const groupMode = group.dataset.modeId || '';
            const showGroup = !selectedMode || groupMode === selectedMode;
            if (showGroup) {
                mapSelect.appendChild(group);
            } else if (group.parentNode) {
                group.parentNode.removeChild(group);
            }
        });
    }

    global.filterMapOptgroupsByMode = filterMapOptgroupsByMode;
})(window);
