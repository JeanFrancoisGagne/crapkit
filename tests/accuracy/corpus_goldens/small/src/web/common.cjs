// A CommonJS module file.
function pad(text, width, fill) {
  if (text.length >= width) {
    return text;
  }
  return (fill || " ").repeat(width - text.length) + text;
}

function table(rows) {
  return rows.map((row) => row.map((cell) => pad(String(cell), 6)).join("|")).join("\n");
}

module.exports = { pad, table };
