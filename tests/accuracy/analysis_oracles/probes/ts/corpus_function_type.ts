export const wrap = () => {
  const e = (d: (q: number) => number) => {
    if (d) {
      return 1;
    }
    return 0;
  };
  return e;
};
