var config = {
  _id: "rs0",
  members: [{ _id: 0, host: "localhost:27017" }],
};

function isAlreadyInitialized(error) {
  if (!error || !error.message) {
    return false;
  }

  return (
    error.message.indexOf("already initialized") !== -1 ||
    error.message.indexOf("already initiated") !== -1 ||
    error.message.indexOf("already exists") !== -1
  );
}

try {
  var status = rs.status();
  if (status.ok === 1) {
    quit(0);
  }
} catch (error) {
  // Replica set not initialized yet.
}

try {
  rs.initiate(config);
} catch (error) {
  if (!isAlreadyInitialized(error)) {
    throw error;
  }
}

for (var attempt = 0; attempt < 60; attempt += 1) {
  try {
    if (rs.status().ok === 1) {
      quit(0);
    }
  } catch (error) {
    // Wait for the replica set to settle.
  }

  sleep(1000);
}

throw new Error("MongoDB replica set rs0 did not become ready in time.");
