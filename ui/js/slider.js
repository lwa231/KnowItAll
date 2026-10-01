// A range input that moves along a fixed list of stops (HyperUI "Range Inputs", see ui/README.md).
// The input's value is the index of the stop; the saved setting is stops[index].
import { state } from './state.js';
import { setSetting, addBinding } from './settings.js';

/** Index of the stop closest to a saved value (an older version may have saved one that is between stops). */
export function nearestIndex(stops, value) {
    let best = 0;
    stops.forEach((stop, i) => { if (Math.abs(stop - value) < Math.abs(stops[best] - value)) best = i; });
    return best;
}

/**
 * Bind `input` (type=range) to settings[key].
 *   format(value)   text shown in the <output> beside the slider
 *   valueText(value) spoken form for aria-valuetext (defaults to format)
 *   marks           [[stopIndex, label], ...] drawn under the track, at the stop positions
 * A saved value between two stops shows the nearest stop but is left alone until the person moves the slider.
 */
export function bindSteppedSlider(input, key, stops, format, { valueText = format, marks = [] } = {}) {
    const output = input.closest('.slider-row').querySelector('output');
    const last = stops.length - 1;
    input.min = '0'; input.max = String(last); input.step = '1';
    const show = value => { output.textContent = format(value); input.setAttribute('aria-valuetext', valueText(value)); };

    const marksBox = input.closest('.setting-slider')?.querySelector('.marks');
    if (marksBox) {
        marksBox.innerHTML = '';
        for (const [index, label] of marks) {
            const span = document.createElement('span');
            span.textContent = label;
            span.style.left = `${(index / last) * 100}%`;
            marksBox.append(span);
        }
    }

    const paint = () => {
        if (document.activeElement === input && input.dataset.dragging) return;
        input.value = String(nearestIndex(stops, state.settings[key]));
        show(state.settings[key]);                              // the real saved value, even if it is not a stop
    };
    input.addEventListener('input', () => { input.dataset.dragging = '1'; show(stops[Number(input.value)]); });
    input.addEventListener('change', () => { delete input.dataset.dragging; setSetting(key, stops[Number(input.value)]); });
    addBinding(paint);
    paint();
}
