# Base UI Migration Evaluation

## Executive Summary

This document evaluates the migration from vanilla TypeScript/CSS to Base UI for the NetPulse frontend. After analysis, **partial adoption is recommended** - Base UI is well-suited for interactive components but the current data visualization approach should be preserved.

## Current Frontend Architecture

### Tech Stack
- **Framework**: Vanilla TypeScript with Vite
- **Styling**: Custom CSS (364 lines)
- **Data Visualization**: Chart.js (dependency present, not yet used extensively)
- **Package**: `@netpulse/web` in Bun monorepo

### Component Inventory

| Component Type | Current Implementation | Count | Migration Candidate |
|----------------|----------------------|-------|---------------------|
| Metric Cards | DOM manipulation + CSS grid | 8 | Yes |
| Status Badge | CSS class toggle | 1 | Yes - Switch/Badge |
| Dropdown Select | Native `<select>` | 1 | Yes - Select |
| Buttons | Custom CSS | 3 | Yes - Button |
| Section Panels | CSS-styled `<section>` | 5 | Partial - Accordion |
| Health Circle | Custom CSS border | 1 | Yes - Progress/Meter |
| Info Grid | CSS grid layout | 2 | No - layout only |
| Error Display | CSS + conditional | 1 | Yes - Toast |

### Key Files
- `apps/web/index.html` - 181 lines, semantic HTML structure
- `apps/web/src/main.ts` - 271 lines, DOM manipulation + data fetching
- `apps/web/src/styles.css` - 364 lines, custom design system
- `apps/web/src/types.ts` - 55 lines, TypeScript interfaces

## Base UI Analysis

### Relevant Components (from llms.txt)

| Base UI Component | Use Case in Dashboard |
|-------------------|----------------------|
| `Button` | Generate report, Download PDF/JSON |
| `Select` | Duration dropdown (24h/7d/30d) |
| `Progress` | Health score visualization |
| `Meter` | Signal strength indicators |
| `Tabs` | Could organize 5G/4G/Connection sections |
| `Toast` | Error notifications |
| `Tooltip` | Signal metric explanations |
| `Switch` | Future: toggle features |

### Base UI Strengths
1. **Unstyled by default** - Works with existing CSS design system
2. **Accessibility built-in** - ARIA attributes, keyboard navigation
3. **Composable** - Can wrap around existing components
4. **TypeScript support** - Strong typing aligns with project
5. **React-based** - Industry standard, maintainable

### Base UI Considerations
1. **Requires React** - Current app is vanilla TS with DOM manipulation
2. **Bundle size** - Adds React + ReactDOM (~45KB gzipped)
3. **Learning curve** - Team must adopt React patterns
4. **Migration effort** - Significant rewrite of main.ts

## Migration Decision Matrix

### Option A: Full Migration (Not Recommended)
- Rewrite entire frontend to React + Base UI
- Effort: High (2-3 days)
- Risk: High - may introduce regressions
- Benefit: Full Base UI ecosystem

### Option B: Partial Migration (Recommended)
- Add React/Base UI for new features
- Keep existing vanilla TS for real-time signal display
- Use React islands pattern (react-dom/client)
- Effort: Medium (1 day)
- Risk: Low - incremental approach

### Option C: No Migration
- Continue with vanilla TS + custom CSS
- Add accessibility manually
- Effort: Low
- Risk: Technical debt accumulation

## Recommended Migration Plan

### Phase 1: Setup (Immediate)
1. Add React and Base UI dependencies
2. Configure Vite for mixed vanilla/React
3. Create React mounting point in HTML

### Phase 2: Interactive Components (Week 1)
Migrate in order of complexity:
1. **Report Controls** - Select + Button
2. **Error Toast** - Toast component
3. **Download Actions** - Button components

### Phase 3: Data Visualization (Week 2)
1. **Health Score** - Progress/Meter
2. **Signal Metrics** - Meter components with tooltips

### Phase 4: Optional Enhancements (Future)
1. **Tabs** for section organization
2. **Accordion** for collapsible sections
3. **Dialog** for detailed reports

## Prototype: Select Component

```tsx
// apps/web/src/components/DurationSelect.tsx
import { Select } from '@base-ui-components/react/select';

interface DurationSelectProps {
  value: string;
  onChange: (value: string) => void;
}

export function DurationSelect({ value, onChange }: DurationSelectProps) {
  const options = [
    { value: '24h', label: 'Last 24 hours' },
    { value: '7d', label: 'Last 7 days' },
    { value: '30d', label: 'Last 30 days' },
  ];

  return (
    <Select.Root value={value} onValueChange={onChange}>
      <Select.Trigger className="report-select">
        <Select.Value />
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner>
          <Select.Popup className="select-popup">
            {options.map((opt) => (
              <Select.Item key={opt.value} value={opt.value}>
                <Select.ItemText>{opt.label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
```

## Dependencies to Add

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@base-ui-components/react": "^1.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0"
  }
}
```

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| React bundle size | Use code splitting for React components |
| Real-time updates | Keep vanilla TS for WebSocket/polling |
| CSS conflicts | Base UI is unstyled, uses existing CSS |
| Testing complexity | Add React Testing Library incrementally |

## Success Metrics

1. **Accessibility**: WCAG 2.1 AA compliance for interactive elements
2. **Bundle size**: <100KB additional gzipped
3. **Performance**: No degradation in signal update latency
4. **Developer experience**: Improved component reusability

## Conclusion

Base UI is a good fit for NetPulse's interactive components, but a full migration is not recommended due to the working real-time signal display code. The hybrid approach (React islands) allows incremental adoption while preserving stability.

### Next Steps
1. Create issue for Phase 1 setup
2. Prototype Select component in isolation
3. Measure bundle size impact
4. Get team feedback on hybrid approach
