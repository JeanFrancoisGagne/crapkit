<template>
  <p>equivalence</p>
</template>

<script>
export default {
  methods: {
    straight(a) {
      const b = a + 1;
      return b;
    },

    fourDeep(a, b, c, d) {
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
    },

    nestedLoops(n) {
      let t = 0;
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
          for (let k = 0; k < n; k++) {
            t++;
          }
        }
      }
      return t;
    },

    flatSeven(a) {
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
    },
  },
};
</script>
