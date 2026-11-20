export function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  if (value === 0) {
    return "0";
  }
  if (Math.abs(value) < 0.0001) {
    return value.toExponential(2);
  }
  return value.toFixed(digits);
}

export function formatPValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  if (value < 0.001) {
    return "<0.001";
  }
  return value.toFixed(3);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

export function formatCI(ci: [number | null, number | null] | null | undefined): string {
  if (!ci || ci[0] === null || ci[1] === null) {
    return "—";
  }
  return `${formatNumber(ci[0])}–${formatNumber(ci[1])}`;
}

export function slugifyModel(model: string): string {
  return model.trim().toLowerCase();
}
