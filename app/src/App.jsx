import picks from './picks.json'

// picks.json is the app's entire state. Claude rewrites it in conversation
// (querying data/clueless.db via scripts/clueless-data) and Vite hot-reloads.

function ItemCard({ item }) {
  return (
    <div className="card">
      <div className="card-img">
        <img src={`/images/${item.item_id}.jpg`} alt={item.name} loading="lazy" />
      </div>
      <div className="card-body">
        <span className="chip">{item.category}</span>
        <p className="name">{item.name}</p>
        {item.reason && <p className="reason">{item.reason}</p>}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <main>
      <header>
        <h1>Clueless</h1>
        <p className="headline">{picks.headline}</p>
        <p className="updated">updated {picks.updated}</p>
      </header>

      {picks.sections.map((s) => (
        <section key={s.heading}>
          <h2>{s.heading}</h2>
          {s.blurb && <p className="blurb">{s.blurb}</p>}
          <div className="row">
            {s.items.map((it) => <ItemCard key={it.item_id} item={it} />)}
          </div>
        </section>
      ))}

      <footer>
        Ask Claude for something (“show me formal shoes”, “build me a summer
        outfit”) and this page updates.
      </footer>
    </main>
  )
}
