import logging
import pymongo


def init_db(db: pymongo.database.Database):
    # Following collections are expected:
    # summaries -> list of all records
    # data_status -> status of each date
    # correlated_data -> correlated data for each IP/rule pair

    # Ensure indexes exist
    db['summaries'].create_index("date")
    db['summaries'].create_index("expires_at", expireAfterSeconds=0)
    db['data_status'].create_index("date", unique=True)
    db['correlated_data'].create_index([("ip", 1), ("rule_id", 1), ("direction", 1)])
    db['correlated_data'].create_index("expires_at", expireAfterSeconds=0)

    # Initialize data_status from existing summaries
    logging.info("Initializing data_status from existing summaries...")
    pipeline = [
        {'$group': {
            '_id': '$date',
            'count': {'$sum': 1},
            'uploaded_at': {'$first': '$uploaded_at'}
        }}
    ]
    results = list(db['summaries'].aggregate(pipeline))
    for res in results:
        db['data_status'].update_one(
            {'date': res['_id']},
            {'$set': {
                'status': 'present',
                'count': res['count'],
                'uploaded_at': res['uploaded_at']
            }},
            upsert=True
        )

    # initialize correlated_data if empty
    if db['correlated_data'].count_documents({}) == 0:
        build_correlations(db)

    logging.info(f"Database initialization complete.")
    return db


def build_correlations(db: pymongo.database.Database):
    logging.info("building correlated_data from summaries...")
    # This will be slow for millions of records

    # We need two passes, one for src and one for dst
    for direction in ['src', 'dst']:
        ip_field = '$src_ip' if direction == 'src' else '$dest_ip'
        pipeline_corr = [
            {'$group': {
                '_id': {'ip': ip_field, 'rule': '$rule'},
                'hit_count': {'$sum': '$count'},
                'last_seen': {'$max': '$date'},
                'first_seen': {'$min': '$date'},
                'expires_at': {'$max': '$expires_at'}
            }}
        ]
        for item in db['summaries'].aggregate(pipeline_corr):
            db['correlated_data'].update_one(
                {
                    'ip': item['_id']['ip'],
                    'rule_id': item['_id']['rule'],
                    'direction': direction
                },
                {
                    '$set': {
                        'hit_count': item['hit_count'],
                        'last_seen': item['last_seen'],
                        'first_seen': item['first_seen'],
                        'expires_at': item['expires_at']
                    }
                },
                upsert=True
            )
    logging.info("Correlated data initialization complete")
