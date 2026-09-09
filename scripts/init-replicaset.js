// Inicializa el replica set rsfraude. Idempotente: si ya existe, no hace nada.
try {
  rs.status();
  print("rsfraude ya estaba iniciado.");
} catch (e) {
  if (e.codeName !== "NotYetInitialized") throw e;
  const r = rs.initiate({
    _id: "rsfraude",
    members: [
      { _id: 0, host: "localhost:27018", priority: 2 },
      { _id: 1, host: "localhost:27019" },
      { _id: 2, host: "localhost:27020" }
    ]
  });
  print("rs.initiate() -> ok=" + r.ok);
}
