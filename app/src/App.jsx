import picks from './picks.json'

// picks.json is the app's entire state. Claude rewrites it in conversation
// (querying data/clueless.db via scripts/clueless-data) and Vite hot-reloads.
// Look: the "Classical" design system's Wardrobe demo — Cher's wardrobe
// computer from Clueless (1995), rendered editorial.

function SlotCard({ item, index, count }) {
  return (
    <article className="slot">
      <div className="plate slot-plate">
        <img src={`/images/${item.item_id}.jpg`} alt={item.name} loading="lazy" />
      </div>
      <div className="slot-copy">
        <div className="slot-kicker">
          {item.category || 'piece'} · <span className="tnum">{index + 1} of {count}</span>
        </div>
        <h3 className="slot-name">{item.name}</h3>
        {item.reason && <p className="slot-reason">{item.reason}</p>}
      </div>
      <div className="slot-arrows" aria-hidden="true">
        <span className="arrow">‹</span>
        <span className="arrow">›</span>
      </div>
    </article>
  )
}

export default function App() {
  const [first, ...rest] = picks.sections
  const categories = [...new Set(
    picks.sections.flatMap((s) => s.items.map((i) => i.category)).filter(Boolean)
  )]

  return (
    <div className="wardrobe">
      <div className="rail rail-left" aria-hidden="true" />
      <div className="page">
        <header className="masthead">
          <div>
            <span className="brand">The Wardrobe</span>
            <span className="issue tnum">Clueless · No. 01</span>
          </div>
          <p className="headline">{picks.headline}</p>
          <p className="updated tnum">updated {picks.updated}</p>
        </header>

        {first && (
          <section className="ensemble">
            <div className="section-rule">
              <h2>{first.heading}</h2>
              {first.blurb && <p className="blurb">{first.blurb}</p>}
            </div>
            {first.items.map((it, i) => (
              <SlotCard key={it.item_id} item={it} index={i} count={first.items.length} />
            ))}
          </section>
        )}

        {rest.map((s) => (
          <section key={s.heading} className="drawer">
            <div className="section-rule">
              <h2>{s.heading}</h2>
              {s.blurb && <p className="blurb">{s.blurb}</p>}
            </div>
            <div className="drawer-row">
              {s.items.map((it) => (
                <figure key={it.item_id} className="swap">
                  <div className="plate swap-plate">
                    <img src={`/images/${it.item_id}.jpg`} alt={it.name} loading="lazy" />
                  </div>
                  <figcaption>
                    <div className="slot-kicker">{it.category}</div>
                    <div className="swap-name">{it.name}</div>
                    {it.reason && <p className="slot-reason">{it.reason}</p>}
                  </figcaption>
                </figure>
              ))}
            </div>
          </section>
        ))}

        <footer className="ticker">
          {(categories.length ? categories : ['shoes', 'jewelry', 'scarves', 'pants', 'sweaters']).map((c) => (
            <span key={c}>{c}</span>
          ))}
        </footer>
        <p className="colophon">
          Ask Claude for something (“show me formal shoes”, “build me a summer outfit”) and this page updates.
        </p>
      </div>
      <div className="rail rail-right" aria-hidden="true" />
    </div>
  )
}
