export function straight(a) {
  const b = a + 1;
  return b;
}

export function fourDeep(a, b, c, d) {
  if (a) {
    if (b) {
      if (c) {
        if (d) {
          return 1;
        }
      }
    }
  }
  return 0;
}

export function nestedLoops(n) {
  let t = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      for (let k = 0; k < n; k++) {
        t++;
      }
    }
  }
  return t;
}

export function flatSeven(a) {
  let n = 0;
  if (a === 1) {
    n++;
  }
  if (a === 2) {
    n++;
  }
  if (a === 3) {
    n++;
  }
  if (a === 4) {
    n++;
  }
  if (a === 5) {
    n++;
  }
  if (a === 6) {
    n++;
  }
  if (a === 7) {
    n++;
  }
  return n;
}
