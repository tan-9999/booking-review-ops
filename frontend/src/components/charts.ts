// Chart tokens. Meaning is fixed across the whole dashboard:
//   blue = good (positive reviews, praise), red = bad (negative reviews, complaints),
//   grey = neutral midpoint (mixed). Validated with the dataviz palette checker:
//   blue/red/grey pass lightness, colour-blind (CVD) and normal-vision separation;
//   grey is intentionally low-chroma (neutral midpoint) and under 3:1 contrast, so
//   every chart also has a legend with text labels and a "Show as table" view.
export const COLORS = {
  positive: "#257de0",
  mixed: "#9c9c9c",
  negative: "#ec4647",
  line: "#257de0",
  grid: "#e6e6e6",
  axis: "#666666",
  text: "#191919",
  surface: "#ffffff",
};

export const axisTick = { fill: COLORS.axis, fontSize: 12 };
