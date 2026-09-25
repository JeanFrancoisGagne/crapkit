<template>
  <button :class="tone(count)" @click="bump">{{ caption(count, limit) }}</button>
</template>

<script>
export function tone(count) {
  if (count > 10) {
    return "hot";
  }
  if (count > 0) {
    return "warm";
  }
  return "cold";
}

export function caption(count, limit) {
  return count >= limit ? `${limit}+` : `${count}`;
}

export default {
  data() {
    return { count: 0, limit: 99 };
  },
  methods: {
    bump() {
      if (this.count < this.limit) {
        this.count += 1;
      }
    },
    tone,
    caption,
  },
};
</script>
