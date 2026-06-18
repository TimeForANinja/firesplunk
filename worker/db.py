import logging
import pymongo


def init_db(db: pymongo.database.Database, index_manager=None):
    # Following collections are expected:
    # summaries -> list of all records
    # data_status -> status of each date
    # correlated_rule_ip -> correlated data for each IP/rule pair
    # correlated_rule_ports -> correlated data for each rule/port pair

    # Ensure indexes exist
    db['summaries'].create_index("date")
    db['summaries'].create_index([("src_ip", 1), ("dest_ip", 1), ("rule", 1), ("date", 1)], unique=True)
    db['summaries'].create_index("expires_at", expireAfterSeconds=0)
    db['data_status'].create_index("date", unique=True)
    db['correlated_rule_ip'].create_index([("ip", 1), ("rule_id", 1), ("direction", 1)], unique=True)
    db['correlated_rule_ip'].create_index("expires_at", expireAfterSeconds=0)
    db['correlated_rule_ports'].create_index([("rule_id", 1), ("port", 1)], unique=True)
    db['correlated_rule_ports'].create_index("expires_at", expireAfterSeconds=0)
    db['tasks'].create_index("created_at")
    db['tasks'].create_index("last_state_change")

    logging.info(f"Database initialization complete.")
    return db
