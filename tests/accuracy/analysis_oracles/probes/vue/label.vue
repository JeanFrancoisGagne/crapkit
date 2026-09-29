<template>
  <p>{{ label }}</p>
</template>

<script>
export default {
  props: ["n"],
  computed: {
    label() {
      if (this.n > 1) {
        return "many";
      }
      return "one";
    },
  },
};
</script>
