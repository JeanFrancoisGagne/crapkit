function outerWithCallbacks(items) {
  return items.map((x) => x * 2).filter((x) => x > 1 && x < 9);
}

const handlers = [
  (a) => a || 0,
  (b) => b && 1,
];

function declared(a, b) {
  if (a) {
    return b;
  }
  return null;
}

var expressed = function (a) {
  return a ? 1 : 0;
};

function* generator(n) {
  for (let i = 0; i < n; i++) {
    yield i;
  }
}

async function fetched(url) {
  try {
    return await fetch(url);
  } catch (e) {
    return null;
  }
}

module.exports = { outerWithCallbacks, handlers, declared, expressed, generator, fetched };
