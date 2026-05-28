# FireSplunk

Optimize and Summarize existing Splunk Eventlogs to fit operational needs.

## Architecture
- **Backend & Frontend:** APIFlask (Python) serving static HTML/JS UI, with MongoDB for persistence.
- **Server:** Gunicorn WSGI.
- **Deployment:** Docker & Docker Compose.
- **CI/CD:** GitHub Actions for container builds.

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
