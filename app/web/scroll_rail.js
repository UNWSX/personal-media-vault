(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MediaVaultScroll = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function metrics(totalHeight, viewportHeight, trackHeight, minimumThumb = 38) {
    const total = Math.max(0, Number(totalHeight) || 0);
    const viewport = Math.max(1, Number(viewportHeight) || 1);
    const track = Math.max(1, Number(trackHeight) || 1);
    const maxScroll = Math.max(0, total - viewport);
    const ratio = Math.min(1, viewport / Math.max(total, viewport));
    const thumbHeight = Math.min(track, Math.max(minimumThumb, Math.round(track * ratio)));
    return {
      total,
      viewport,
      track,
      maxScroll,
      ratio,
      thumbHeight,
      travel: Math.max(0, track - thumbHeight),
    };
  }

  function thumbOffset(scrollTop, metric) {
    if (!metric || metric.maxScroll <= 0 || metric.travel <= 0) return 0;
    return Math.round(
      clamp(Number(scrollTop) || 0, 0, metric.maxScroll)
        / metric.maxScroll
        * metric.travel,
    );
  }

  function scrollTopForOffset(offset, metric) {
    if (!metric || metric.maxScroll <= 0 || metric.travel <= 0) return 0;
    return clamp(Number(offset) || 0, 0, metric.travel)
      / metric.travel
      * metric.maxScroll;
  }

  function scrollTopForPointer(pointerY, railTop, metric) {
    if (!metric || metric.maxScroll <= 0) return 0;
    const targetTop = Number(pointerY) - Number(railTop) - metric.thumbHeight / 2;
    return scrollTopForOffset(targetTop, metric);
  }

  return { clamp, metrics, thumbOffset, scrollTopForOffset, scrollTopForPointer };
});
