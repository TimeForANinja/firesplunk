# FireSplunk

Optimize and Summarize existing Splunk Eventlogs to fit operational needs.

## Architecture
- **Backend & Frontend:** APIFlask (Python) serving a responsive Tailwind-based UI.
- **Database:** MongoDB for high-performance storage of summarized firewall logs.
- **Server:** Gunicorn WSGI.
- **Deployment:** Docker & Docker Compose.

## Database Structure
FireSplunk uses three main collections in MongoDB to balance storage efficiency and query performance:

1. **`summaries`**: The primary storage for uploaded data.
   - Stores granular records: `date`, `src_ip`, `dest_ip`, `rule`, `count`, `ports`, and `expires_at`.
   - Indexed by `date` for fast timeline retrieval.
   - Automatically expires records via TTL index on `expires_at`.

2. **`correlated_rule_ip`**: A high-performance aggregation layer for IP-Rule activity.
   - Stores activity maps: `ip`, `rule`, `direction`, `activity` (dict mapping date to count), `expires_at`.
   - Used to power activity tables and timelines instantly.
   - Updated atomically during CSV uploads using `$inc`.

3. **`correlated_rule_ports`**: Aggregation layer for Rule-Port activity.
   - Stores activity maps: `rule`, `port`, `activity` (dict mapping date to count), `expires_at`.
   - Powers the "Involved Ports" view in rule searches.

4. **`data_status`**: Tracks the completeness of the local database.
   - Stores metadata for each day: `status` (present/missing/locked), `count`, `uploaded_at`.
   - Powers the Data Management view and provides warnings if searches cover periods with missing data.

## How to Use
1. **Initial Setup:** Run `docker-compose up -d`. On the first start with existing data, the backend will run an `init_db` process to build indexes and populate the `correlated_rule_ip` collection. **Please be patient** and wait for the "Database initialization complete" message in the logs before performing heavy searches.
2. **Identify Gaps:** Navigate to the **Data Management** tab. This shows a 30-day lookback. Red rows indicate missing data.
3. **Fetch from Splunk:** Unfold a "missing" row to find the pre-generated Splunk query and a direct link. Run the query in your Splunk instance and export the results as a **CSV**.
4. **Upload Data:** Drag and drop the exported CSV into the upload area on the Data Management page. The system will process the data in batches and update both the `summaries` and `correlated_rule_ip` collections.
5. **Search & Analyze:** Use the **Search IP** or **Search Rule** tabs to investigate activity. If you see a warning about "missing data", check the Data Management tab to see which days need to be uploaded for a complete picture.

## Project Structure
- `/backend`: APIFlask application, static assets, and Dockerfile.
- `docker-compose.yml`: Orchestrates the app and database.

## Quick Start
1. Ensure you have Docker and Docker Compose installed.
2. Clone the repository.
3. Run `docker-compose up --build`.
4. Access the **App** at `http://localhost`.
5. Access the **API Documentation** at `http://localhost/docs`.

## Key Features
- **Data Management:** Consolidated view for identifying missing data, downloading Splunk queries, and uploading results (CSV drag-and-drop or JSON).
- **IP Search:** 3-panel view showing activity timeline, source rule hits, and destination rule hits.
- **Rule Search:** 3-panel view showing activity timeline, active source IPs, and active destination IPs.

## Configuration
Configuration is done via environment variables.
- `MONGO_URI`: MongoDB connection string (default: `mongodb://mongodb:27017/`).
- `SPLUNK_SERVER_URL`: Base URL for your Splunk instance.
- `SPLUNK_QUERY_TEMPLATE`: The base Splunk query to use (default: `index=net-fw | stats count by src_ip dest_ip rule`).
- `PORT`: Backend port (default: `5000`).
