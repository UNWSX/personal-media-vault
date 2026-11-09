(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MediaVaultCompat = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const EDITABLE_FIELD_KEYS = [
    "comment", "score", "myScore", "doubanScore", "metacriticScore",
    "imdbScore", "bangumiScore", "rottenTomatoesScore", "status", "progressStatus", "finishTime",
    "startDate", "platform", "purchaseDate", "price",
  ];

  function sameValue(a, b) {
    return JSON.stringify(a == null ? null : a) === JSON.stringify(b == null ? null : b);
  }

  function conflictGroup(patch, item) {
    const keys = new Set(Object.keys(patch).filter((key) => key !== "revision"));
    if (keys.has("status") || keys.has("progressStatus")) {
      keys.add("status");
      keys.add("progressStatus");
    }
    if (keys.has("score") || keys.has("doubanScore") || keys.has("metacriticScore")) {
      keys.add("score");
      if (item && item.type === "game") keys.add("metacriticScore");
      else keys.add("doubanScore");
    }
    return [...keys];
  }

  function dirtyKeysBetween(base, local, initial) {
    const keys = new Set(initial || []);
    EDITABLE_FIELD_KEYS.forEach((key) => {
      if (!sameValue(base && base[key], local && local[key])) keys.add(key);
    });
    return [...keys];
  }

  function mergeLatestWithLocal(latest, local, dirtyKeys) {
    const merged = Object.assign({}, latest);
    dirtyKeys.forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(local, key)) merged[key] = local[key];
    });
    merged.revision = latest.revision;
    return merged;
  }

  function threeWayDecision(baseValue, localValue, latestValue) {
    if (sameValue(localValue, baseValue)) return "accept_latest";
    if (sameValue(latestValue, baseValue) || sameValue(localValue, latestValue)) {
      return "accept_local";
    }
    return "conflict";
  }

  function groupValue(item, keys) {
    return keys.map((key) => {
      if (!item || item[key] == null) return null;
      return item[key];
    });
  }

  function threeWayGroupDecision(base, local, latest, keys) {
    return threeWayDecision(
      groupValue(base, keys),
      groupValue(local, keys),
      groupValue(latest, keys),
    );
  }

  function networkSortScore(item) {
    if (!item) return null;
    if (item.imdbScore != null) return Number(item.imdbScore) * 10;
    if (item.doubanScore != null) return Number(item.doubanScore) * 10;
    if (item.metacriticScore != null) return Number(item.metacriticScore);
    if (item.score == null) return null;
    return item.type === "game" && Number(item.score) > 10
      ? Number(item.score)
      : Number(item.score) * 10;
  }

  function seriesSortKey(item) {
    const raw = String(item && item.title || "")
      .normalize("NFKC")
      .split(/[\/|｜／]/, 1)[0]
      .trim();
    const groupTitle = raw.split(/\s*[:：]\s*/, 1)[0].trim() || raw;
    let title = groupTitle;
    title = title.replace(/\s*第[零〇一二三四五六七八九十百\d]+[季期].*$/, "");
    title = title.replace(/\s*(?:Season\s*\d+|S\d+)\b.*$/i, "");
    title = title.replace(/[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]/g, "");
    title = title.replace(/\s*(?:Part\s*\.?\s*|第\s*)[0-9一二三四五六七八九十百]+\s*(?:章|篇|部)?\s*$/i, "");
    title = title.replace(/\s*(?:剧场版|劇場版|最终季|最終季|完结篇|完結篇)\s*$/i, "");
    title = title.replace(/\s*(?:复刻版|重制版|重置版|皇家版|终极版|完全版|决定版|导演剪辑版|導演剪輯版|特别版|特別版|周年纪念版|高清版|豪华版|豪華版|黄金版|年度版|试玩版|重制|复刻)$/i, "");
    title = title.replace(/\s*\d+$/, "");
    title = title.replace(/\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|[IVX]+)$/, "");
    return title.trim() || groupTitle;
  }

  return {
    EDITABLE_FIELD_KEYS,
    sameValue,
    conflictGroup,
    dirtyKeysBetween,
    mergeLatestWithLocal,
    threeWayDecision,
    threeWayGroupDecision,
    networkSortScore,
    seriesSortKey,
  };
});
